"""
Class which implements the pause/unpause operations for release pipeline systems,
including backing S3 storage and GoCD integration.
"""
from abc import ABCMeta, abstractmethod
from collections import defaultdict
from datetime import datetime
import hashlib
import logging
import os.path
import yaml

import boto
from boto.exception import S3ResponseError
from boto.s3.key import Key
from boto.s3.bucketlistresultset import bucket_lister

from alton.gocd_api import GoCDAPI


log = logging.getLogger(__name__)


# Official list of pipeline systems.
# Only these pipeline systems are allowed to be paused or resumed.
# To add the ability to pause/resume another pipeline system, add it to this dictionary.
# Keys are pipeline system names.
# Values are a list of pipeline names to pause/resume when the system is paused/resumed.
PIPELINE_SYSTEM_INFO = {
    'edxapp': [
        'edxapp_release_advancer',
        'prerelease_edxapp_materials_latest',
        'edxapp_cut_release_candidate'
    ]
}

# pylint: disable=len-as-condition


class PauseEventNotFound(Exception):
    """
    Raised when a pause event with a specified event ID isn't found.
    """
    pass


class HistoricalEventNotFound(Exception):
    """
    Raised when a historical event cannot be found for an added pause event.
    """
    pass


class MultiplePauseEventsFound(Exception):
    """
    Raised when multiple pause events are found with the same event ID.
    """
    pass


class PauseEventOps(object):
    """
    Abstract interface class for pausing operations.
    """
    __metaclass__ = ABCMeta

    @abstractmethod
    def add_pipeline_event(self, who_paused, pipeline_system, pause_reason):
        """
        Pauses a pipeline system, stopping it from releasing.

        Arguments:
            who_paused (str): HipChat username which paused the pipeline.
            pipeline_system (str): Pipeline system name to pause, e.g. edxapp, ecommerce, etc.
            pause_reason (str): Reason why pipeline system was paused.

        Returns:
            status (dict): Dictionary containing the keys:
                event_id (str): Event ID of added pause event.
        """
        return

    @abstractmethod
    def remove_pipeline_event(self, who_removed, event_id):
        """
        Removes a previously-created pipeline pause event, which may unpause a pipeline system if no more
        pause events remain.

        Arguments:
            who_removed (str): HipChat username who removed the event.
            event_id (str): ID of the pipeline event to remove.

        Returns:
            response (str): Message to display to user

        Raises:
            PauseEventNotFound:
                When the passed-in event ID is not found.
            MultiplePauseEventsFound:
                When the passed-in event ID has multiple S3 files.
        """
        return

    @abstractmethod
    def pipeline_status(self, pipeline_system, paused_only):
        """
        Returns the status of one or all pipeline systems, optionally filtered by paused pipeline systems only.

        Arguments:
            pipeline_system (str):
                Pipeline system name for which to return status, e.g. edxapp, ecommerce, etc., None for all systems
            paused_only (bool):
                True to return only the status of paused pipelines, False to return paused & unpaused.

        Returns:
            dict(pipeline_system: list()):
                Dictionary with:
                    keys: pipeline_system names
                    values: lists of pipeline events as dicts
        """
        return {}


class S3PauseEventOps(PauseEventOps):
    """
    Encapsulates S3 operations needed to pause/unpause pipeline systems and list pipeline statuses.
    """
    # Top-level directory name for all pause event files in bucket.
    PAUSE_DIRECTORY = 'paused/'

    # Subdirectory name holding the current pause events.
    CURRENT_DIRECTORY = PAUSE_DIRECTORY + 'current/'

    # Subdirectory holding all the historical pause events.
    HISTORY_DIRECTORY = PAUSE_DIRECTORY + 'history/'

    # Common time format to output/parse with strptime/strftime.
    TIME_FORMAT = '%Y-%m-%d_%H:%M:%S'

    def __init__(self, bucket_name, gocd_username, gocd_password, gocd_url):
        self.s3_conn = boto.connect_s3()
        # Get or create the specified bucket.
        try:
            self.pipeline_bucket = self.s3_conn.get_bucket(bucket_name)
        except S3ResponseError as exc:
            if exc.error_code == 'NoSuchBucket':
                self.pipeline_bucket = self.s3_conn.create_bucket(bucket_name)
            else:
                # In all other error cases, re-raise.
                raise
        # Create a GoCD client for pausing/unpausing pipelines.
        self.gocd_client = GoCDAPI(gocd_username, gocd_password, gocd_url)

    def _create_s3_file(self, filepath, str_contents):
        """
        Create an S3 file at the filepath, writing the str_contents string to it.
        """
        s3_file = Key(self.pipeline_bucket, filepath)
        s3_file.set_contents_from_string(str_contents)

    def _delete_s3_file(self, filepath):
        """
        Delete an S3 file at the filepath.
        """
        s3_file = Key(self.pipeline_bucket, filepath)
        s3_file.delete()

    def _s3_file_exists(self, filepath):
        """
        Returns True if filepath exists in the bucket, else False.
        """
        return self.pipeline_bucket.get_key(filepath) is not None

    def _get_current_pause_events(self, pipeline_system=None, event_id=None):
        """
        Returns the current pause status of one or all pipeline systems and one or all events.

        Arguments:
            pipeline_system (str):
                Pipeline system name for which to return status, e.g. edxapp, ecommerce, etc., None for all systems
            event_id (str):
                Event ID for which to return status, None for all events

        Returns:
            dict(pipeline_system: list()):
                Dictionary with:
                    keys: pipeline_system names
                    values: lists of pipeline events as dicts
        """
        pause_status = defaultdict(list)
        for key in bucket_lister(self.pipeline_bucket, prefix=self.CURRENT_DIRECTORY):
            # Only read YAML files - at least, files with a .yml suffix.
            if not key.name.endswith('.yml'):
                continue
            try:
                pause_data = yaml.safe_load(key.get_contents_as_string())
            except yaml.YAMLError:
                log.warning('Unable to load file as YAML: %s - continuing...', key.name)
                continue
            if pause_data is None:
                log.warning('Unable to load file as YAML: %s - continuing...', key.name)
                continue
            # If pipeline system was specified and this pause file isn't for that system, ignore it.
            if pipeline_system and pipeline_system != pause_data['pipeline_system']:
                continue
            # If event ID was specified and this pause file isn't for that event, ignore it.
            if event_id and event_id != pause_data['event_id']:
                continue
            # Add the base key name to the returned pause data.
            pause_data['key_name'] = os.path.basename(key.name)
            # Add the pause data from this file to the returned data.
            pause_status[pause_data['pipeline_system']].append(pause_data)
        return pause_status

    def _make_history_pause_filepath(self, event_datetime, pause_file_name):
        """
        Construct a file path to a historical pause event file.
        """
        return '{history_dir}{year}/{month:02d}/{pause_file}'.format(
            history_dir=self.HISTORY_DIRECTORY,
            year=event_datetime.year,
            month=event_datetime.month,
            pause_file=pause_file_name
        )

    def _make_pause_event_filename(self, event_id, time_str, pipeline_system):
        """
        Construct the file name of a YAML file holding pause event data.
        """
        return '{pipeline_system}_{time_str}_{event_id}.yml'.format(
            event_id=event_id, time_str=time_str, pipeline_system=pipeline_system
        )

    def _add_event_state_ops(self, who_paused, pipeline_system, pause_reason):
        """
        Perform the S3 operations to store the associated state upon the addition of a pipeline pause event.
        """
        # Capture the current date/time as a string.
        current_time = datetime.now()
        current_time_str = current_time.strftime(self.TIME_FORMAT)
        # Hash the date/time to create a unique pause event ID - use the datetime obj for msec-uniqueness.
        event_id = hashlib.sha1(unicode(current_time)).hexdigest()[-8:]
        event_contents = {
            'event_id': event_id,
            'pipeline_system': pipeline_system,
            'who_paused': who_paused,
            'time_paused': current_time_str,
            'who_cleared': None,
            'time_cleared': None,
            'pause_reason': pause_reason,
        }
        # Create the current pause file.
        pause_file_name = self._make_pause_event_filename(event_id, current_time_str, pipeline_system)
        current_pause_filepath = '{current_dir}{pause_file}'.format(
            current_dir=self.CURRENT_DIRECTORY,
            pause_file=pause_file_name
        )
        self._create_s3_file(current_pause_filepath, yaml.safe_dump(event_contents))

        # Create the historical pause file.
        history_pause_filepath = self._make_history_pause_filepath(current_time, pause_file_name)
        self._create_s3_file(history_pause_filepath, yaml.safe_dump(event_contents))

        return event_id

    def _add_event_pipeline_ops(self, event_id, pipeline_system, pause_reason):
        """
        Perform the GoCD pipeline operations to pause a pipeline system upon the addition of a pipeline pause event.
        """
        # Always pause the GoCD pipelines, irregardless if the pipeline system is already paused.
        # Pause each specified pipeline in the pipeline system.
        for pipeline_name in PIPELINE_SYSTEM_INFO[pipeline_system]:
            log.info(
                "Pause event '%s' for pipeline system '%s' - pausing pipeline '%s'.",
                event_id, pipeline_system, pipeline_name
            )
            self.gocd_client.pause_pipeline(pipeline_name, pause_reason)

    def add_pipeline_event(self, who_paused, pipeline_system, pause_reason):
        """
        Pauses a pipeline system, stopping it from releasing.

        Arguments:
            who_paused (str): HipChat username which paused the pipeline.
            pipeline_system (str): Pipeline system name to pause, e.g. edxapp, ecommerce, etc.
            pause_reason (str): Reason why pipeline system was paused.

        Returns:
            status (dict): Dictionary containing the keys:
                event_id (str): Event ID of added pause event.
        """
        event_id = self._add_event_state_ops(who_paused, pipeline_system, pause_reason)
        self._add_event_pipeline_ops(event_id, pipeline_system, pause_reason)

        pause_status = {
            'event_id': event_id
        }
        log.info(
            "add_pipeline_event: system '%s' with reason '%s' paused by '%s' - status: %s.",
            pipeline_system, pause_reason, who_paused, pause_status
        )
        return pause_status

    def _remove_event_state_ops(self, who_removed, event_id):
        """
        Perform the S3 operations to store the associated state
        upon the removal of a pipeline pause event.
        """
        # Capture the current date/time as a string.
        current_time = datetime.now()
        current_time_str = current_time.strftime(self.TIME_FORMAT)

        # Read in the event with the specified event ID from the current pause events.
        current_event_data = self._get_current_pause_events(event_id=event_id)

        # Ensure one and only one event is returned.
        if len(current_event_data) == 0:
            raise PauseEventNotFound(event_id)
        elif len(current_event_data) > 1 or len(current_event_data[current_event_data.keys()[0]]) > 1:
            raise MultiplePauseEventsFound(event_id)
        pause_data = current_event_data[current_event_data.keys()[0]][0]

        # Update the historical event.
        event_dt = datetime.strptime(pause_data['time_paused'], self.TIME_FORMAT)
        history_pause_filepath = self._make_history_pause_filepath(event_dt, pause_data['key_name'])

        # Sanity check to see if history file already exists - it should!
        history_file = Key(self.pipeline_bucket, name=history_pause_filepath)
        if not history_file.exists():
            raise HistoricalEventNotFound('History file "{}" does not exist - creating.'.format(history_file.name))

        pause_data['time_cleared'] = current_time_str
        pause_data['who_cleared'] = who_removed
        self._create_s3_file(history_pause_filepath, yaml.safe_dump(pause_data))

        # Remove the current event.
        current_event_filepath = '{current_dir}{event_filename}'.format(
            current_dir=self.CURRENT_DIRECTORY,
            event_filename=pause_data['key_name']
        )
        try:
            self._delete_s3_file(current_event_filepath)
        except:  # pylint: disable=bare-except
            # Does the file still exist? If not, continue.
            if self._s3_file_exists(current_event_filepath):
                # File exists but can't be deleted. Re-raise.
                raise
            # File does not exist. Log a failed deletion but continue.
            log.warning(
                "File '%s' failed deletion when removing event '%s' - file did not exist.",
                current_event_filepath, event_id
            )
        return pause_data['pipeline_system']

    def _remove_event_pipeline_ops(self, event_id, pipeline_system):
        """
        Perform the GoCD pipeline operations to perhaps unpause a pipeline system
        upon the removal of a pipeline pause event.
        """
        # Read the current pause events from S3 again for this pipeline system.
        remaining_pause_events = self._get_current_pause_events(pipeline_system)
        # Count them.
        num_remaining_events = sum([len(statuses) for __, statuses in remaining_pause_events.items()])
        # If no more events for the pipeline system, un-pause the GoCD pipelines.
        if num_remaining_events == 0:
            # Unpause the pipeline system.
            for pipeline_name in PIPELINE_SYSTEM_INFO[pipeline_system]:
                log.info(
                    "No events remaining for pipeline system '%s' after removing event '%s' - unpausing pipeline '%s'.",
                    pipeline_system, event_id, pipeline_name
                )
                self.gocd_client.unpause_pipeline(pipeline_name)
        return num_remaining_events

    def remove_pipeline_event(self, who_removed, event_id):
        """
        Removes a previously-created pipeline pause event, which may unpause a pipeline system if no more
        pause events remain.

        Arguments:
            who_removed (str): HipChat username who removed the event.
            event_id (str): ID of the pipeline event to remove.

        Returns:
            status (dict): Dictionary with keys:
                pipeline_system (str): Pipeline system name associated with event ID.
                unpaused (bool): True if removing pause event caused pipeline system to be unpaused.
                num_remaining_events (int): Number of pause events remaining for the pipeline system.

        Raises:
            PauseEventNotFound:
                When the passed-in event ID is not found.
            MultiplePauseEventsFound:
                When the passed-in event ID has multiple S3 files.
        """
        pipeline_system = self._remove_event_state_ops(who_removed, event_id)
        num_remaining_events = self._remove_event_pipeline_ops(event_id, pipeline_system)
        remove_status = {
            'pipeline_system': pipeline_system,
            'unpaused': num_remaining_events == 0,
            'num_remaining_events': num_remaining_events
        }
        log.info(
            "remove_pipeline_event: event ID '%s' removed by '%s' - status: %s",
            event_id, who_removed, remove_status
        )
        return remove_status

    def pipeline_status(self, pipeline_system=None, paused_only=False):
        """
        Returns the status of one or all pipeline systems, optionally filtered by paused pipeline systems only.

        Arguments:
            pipeline_system (str):
                Pipeline system name for which to return status, e.g. edxapp, ecommerce, etc., None for all systems
            paused_only (bool):
                True to return only the status of paused pipelines, False to return paused & unpaused.

        Returns:
            dict(pipeline_system: list()):
                Dictionary with:
                    keys: pipeline_system names
                    values: lists of pipeline events as dicts
        """
        pause_status = self._get_current_pause_events(pipeline_system)

        # Always return the status of any specified pipeline system, even if not paused.
        if pipeline_system and pipeline_system not in pause_status:
            pause_status[pipeline_system] = []

        if not paused_only:
            # Add the pipeline systems which had no current pause files - to indicate they are active.
            for one_system in PIPELINE_SYSTEM_INFO:
                if one_system not in pause_status:
                    pause_status[one_system] = []

        log.info(
            "pipeline_status: system '%s' with paused_only '%s' - returning: %s",
            pipeline_system, paused_only, dict(pause_status)
        )
        return dict(pause_status)
