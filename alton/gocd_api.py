""" Commands to interact with the GoCD API. """
from __future__ import absolute_import
from __future__ import print_function, unicode_literals

import logging

from yagocd import Yagocd as yagocd

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)


class GoCDAPI(object):
    """
    Interacts with the GoCD API to perform common tasks.
    """
    def __init__(self, username, password, go_server_url):
        self.client = yagocd(
            server=go_server_url,
            auth=(username, password),
        )

    def pause_pipeline(self, pipeline_name, cause):
        """
        Pauses the specified pipeline with the specified cause.
        """
        LOG.info("Pausing pipeline '%s' with cause '%s'.", pipeline_name, cause)
        self.client.pipelines.pause(pipeline_name, cause)

    def unpause_pipeline(self, pipeline_name):
        """
        Unpauses the specified pipeline.
        """
        LOG.info("Unpausing pipeline '%s'.", pipeline_name)
        self.client.pipelines.unpause(pipeline_name)
