import argparse
import logging
import re
import subprocess
from datetime import datetime

import requests
from urllib.parse import urlencode


logger = logging.getLogger('archiver')


class DockerApiClient(object):
    def __init__(self):
        self.token = None

    def get_token(self, res, repository, what='pull'):
        m = re.match(
            r'Bearer realm="([^"]+)",service="([^"]+)"',
            res.headers['www-authenticate'],
        )
        if m is None:
            res.raise_for_status()
        scope = 'repository:%s/%s:%s' % (repository[1], repository[2], what)
        res = requests.get(
            m.group(1) + '?' + urlencode({
                'service': m.group(2),
                'scope': scope,
            }),
        )
        res.raise_for_status()
        return res.json()['token']

    def list_tags(self, repository):
        headers = {}
        if self.token is not None:
            headers['Authorization'] = 'Bearer %s' % self.token
        res = requests.get(
            'https://%s/v2/%s/%s/tags/list' % (
                repository[0], repository[1], repository[2],
            ),
            headers=headers,
        )
        if self.token is None and res.status_code == 401:
            self.token = self.get_token(res, repository)
            return self.list_tags(repository)

        res.raise_for_status()

        return res.json()['tags']

    def get_manifest(self, repository, tag):
        headers = {}
        if self.token is not None:
            headers['Authorization'] = 'Bearer %s' % self.token
        res = requests.get(
            'https://%s/v2/%s/%s/manifests/%s' % (
                repository[0], repository[1], repository[2], tag,
            ),
            headers=headers,
        )
        if self.token is None and res.status_code == 401:
            self.token = self.get_token(res, repository)
            return self.list_tags(repository)

        res.raise_for_status()

        return res.json(), res.headers['docker-content-digest']


def copy_image(source_repository, source_tag, target_repository):
    # Generate target tag
    timestamp = datetime.utcnow().strftime('%Y%m%dt%H%M%Sz')
    target_tag = source_tag + '-' + timestamp
    subprocess.check_call([
        'crane', 'copy',
        '%s:%s' % ('/'.join(source_repository), source_tag),
        '%s:%s' % ('/'.join(target_repository), target_tag),
    ])


def parse_repository(image):
    repository = image.split('/')
    if len(repository) == 1:
        repository = ['index.docker.io', 'library'] + repository
    elif len(repository) == 2:
        repository = ['index.docker.io'] + repository
    return repository


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        'archiver',
        description="Archive Docker images from another repository",
    )
    parser.add_argument('source_repository')
    parser.add_argument('target_repository')
    args = parser.parse_args()

    source_repository = parse_repository(args.source_repository)
    target_repository = parse_repository(args.target_repository)

    source_client = DockerApiClient()
    target_client = DockerApiClient()

    # For each tag in the source repository
    tags = source_client.list_tags(source_repository)
    for tag in tags:
        # Get the digest
        manifest, digest = source_client.get_manifest(
            source_repository,
            tag,
        )

        # Check if we have this digest in the target repository
        try:
            target_client.get_manifest(
                target_repository,
                digest,
            )
        except requests.HTTPError as e:
            # Copy the image
            logger.info(
                "%s:%s is missing, copying (%s)",
                '/'.join(source_repository),
                tag,
                digest,
            )
            if e.response.status_code == 404:
                copy_image(
                    source_repository,
                    tag,
                    target_repository,
                )
        else:
            logger.info(
                "%s:%s is present (%s)",
                '/'.join(source_repository),
                tag,
                digest,
            )


if __name__ == '__main__':
    main()
