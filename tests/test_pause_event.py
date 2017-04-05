"""
Tests for release-pausing functionality.
"""

import os.path
from datetime import datetime
import unittest
from moto import mock_s3
from mock import patch, call
from freezegun import freeze_time
import yaml
from alton.pause_event import (
    PauseEventNotFound,
    HistoricalEventNotFound,
    MultiplePauseEventsFound,
    S3PauseEventOps,
    PIPELINE_SYSTEM_INFO
)
from alton.gocd_api import GoCDAPI


class TestS3PauseEventOps(unittest.TestCase):
    """
    Tests all the pause event ops.
    """
    TEST_USER = 'TestUser'
    TEST_PIPELINE_SYSTEM = 'edxapp'
    TEST_S3_BUCKET_NAME = 'pause_operations_bucket'
    TEST_GOCD_USERNAME = 'gocd_test_user'
    TEST_GOCD_PASSWORD = 'gocd_test_password'
    TEST_GOCD_SVR_URL = 'https://gocd.test.edx.org'

    def _create_s3_pause_event_ops_obj(self):
        """
        Construct a standard test S3PauseEventOps object.
        """
        return S3PauseEventOps(
            self.TEST_S3_BUCKET_NAME,
            self.TEST_GOCD_USERNAME,
            self.TEST_GOCD_PASSWORD,
            self.TEST_GOCD_SVR_URL
        )

    @freeze_time("2017-04-07 01:00:00")
    @patch.object(GoCDAPI, 'pause_pipeline')
    @mock_s3
    def test_add_pipeline_event_s3_ops(self, __):
        pause_ops = self._create_s3_pause_event_ops_obj()
        test_reason = 'Paused for a test reason.'
        pause_dt = datetime.now()
        pause_status = pause_ops.add_pipeline_event(
            self.TEST_USER,
            self.TEST_PIPELINE_SYSTEM,
            test_reason
        )
        # Verify the S3 file in the current directory is present and named correctly.
        current_key = pause_ops.pipeline_bucket.get_all_keys(prefix=pause_ops.CURRENT_DIRECTORY)
        self.assertEqual(len(current_key), 1)
        current_key = current_key[0]
        file_name = self.TEST_PIPELINE_SYSTEM
        file_name += '_{}'.format(pause_dt.strftime(pause_ops.TIME_FORMAT))
        file_name += '_{}.yml'.format(pause_status['event_id'])
        self.assertTrue(current_key.name.endswith(file_name))

        # Verify the contents of the current S3 file.
        current_contents = yaml.safe_load(current_key.get_contents_as_string())
        self.assertIsInstance(current_contents['event_id'], basestring)
        self.assertEqual(current_contents['who_paused'], self.TEST_USER)
        self.assertEqual(current_contents['time_paused'], pause_dt.strftime(pause_ops.TIME_FORMAT))
        self.assertEqual(current_contents['pause_reason'], test_reason)
        self.assertEqual(current_contents['pipeline_system'], self.TEST_PIPELINE_SYSTEM)
        self.assertIsNone(current_contents['who_cleared'])
        self.assertIsNone(current_contents['time_cleared'])

        # Verify the S3 file in the historical directory is present and named correctly.
        historical_key = pause_ops.pipeline_bucket.get_all_keys(prefix=pause_ops.HISTORY_DIRECTORY)
        self.assertEqual(len(historical_key), 1)
        historical_key = historical_key[0]
        self.assertEqual(os.path.basename(current_key.name), os.path.basename(historical_key.name))

        # Verify the contents of the historical S3 file.
        historical_contents = yaml.safe_load(historical_key.get_contents_as_string())
        self.assertDictEqual(current_contents, historical_contents)

    @patch.object(GoCDAPI, 'pause_pipeline')
    @mock_s3
    def test_add_pipeline_event_gocd_pause_calls(self, pause_mock):
        pause_ops = self._create_s3_pause_event_ops_obj()
        test_reason = 'Paused for a test reason.'
        pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            test_reason
        )
        expected_calls = [call(pipeline, test_reason) for pipeline in PIPELINE_SYSTEM_INFO['edxapp']]
        pause_mock.assert_has_calls(expected_calls)

    @patch.object(GoCDAPI, 'pause_pipeline')
    @patch.object(GoCDAPI, 'unpause_pipeline')
    @mock_s3
    def test_add_pipeline_event_gocd_unpause_calls(self, unpause_mock, __):
        pause_ops = self._create_s3_pause_event_ops_obj()
        test_reason = 'Paused for a test reason.'
        pause_status = pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            test_reason
        )
        pause_ops.remove_pipeline_event(
            self.TEST_USER, pause_status['event_id']
        )
        expected_calls = [call(pipeline) for pipeline in PIPELINE_SYSTEM_INFO['edxapp']]
        unpause_mock.assert_has_calls(expected_calls)

    @patch.object(GoCDAPI, 'pause_pipeline')
    @patch.object(GoCDAPI, 'unpause_pipeline')
    @mock_s3
    def test_remove_pipeline_event_s3_ops(self, __, ___):
        pause_ops = self._create_s3_pause_event_ops_obj()
        with freeze_time("2017-04-08 05:15:15") as frozen_datetime:
            # Add a pipeline pause event.
            pause_status = pause_ops.add_pipeline_event(
                self.TEST_USER,
                self.TEST_PIPELINE_SYSTEM,
                'Paused for a test reason.'
            )
            # Now remove the same event at a different time.
            frozen_datetime.move_to("2017-04-08 11:42:00")
            remove_dt = datetime.now()
            remove_status = pause_ops.remove_pipeline_event(
                self.TEST_USER, pause_status['event_id']
            )
        self.assertTrue(remove_status['unpaused'])
        self.assertEqual(remove_status['num_remaining_events'], 0)
        self.assertEqual(remove_status['pipeline_system'], self.TEST_PIPELINE_SYSTEM)

        # Ensure the current pause file for the event no longer exists.
        current_key = pause_ops.pipeline_bucket.get_all_keys(prefix=pause_ops.CURRENT_DIRECTORY)
        self.assertEqual(len(current_key), 0)

        # Verify the historical pause file has been updated.
        historical_key = pause_ops.pipeline_bucket.get_all_keys(prefix=pause_ops.HISTORY_DIRECTORY)
        self.assertEqual(len(historical_key), 1)
        historical_key = historical_key[0]

        # Verify the contents of the historical S3 file.
        historical_contents = yaml.safe_load(historical_key.get_contents_as_string())
        self.assertEqual(historical_contents['who_cleared'], self.TEST_USER)
        self.assertEqual(historical_contents['time_cleared'], remove_dt.strftime(pause_ops.TIME_FORMAT))

    @patch.object(GoCDAPI, 'pause_pipeline')
    @patch.object(GoCDAPI, 'unpause_pipeline')
    @mock_s3
    def test_pipeline_interleaved_add_remove_pause_events(self, unpause_mock, __):
        """
        Interleave an event removal with an event addition to guarantee end result.
        """
        pause_ops = self._create_s3_pause_event_ops_obj()
        # First, add an event.
        pause1_status = pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            'Paused for a test reason.'
        )
        # Perform the FIRST part of a removing the added event - the S3 operations.
        # pylint: disable=protected-access
        pipeline_system = pause_ops._remove_event_state_ops(self.TEST_USER, pause1_status['event_id'])
        # Now, add another event.
        pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            'Paused for an interleaving test.'
        )
        # Finally, perform the SECOND part of removing the original event - the unpause pipeline operations.
        num_remaining_events = pause_ops._remove_event_pipeline_ops(pause1_status['event_id'], pipeline_system)
        self.assertEqual(num_remaining_events, 1)
        # Verify that the pipeline was *not* unpaused, due to the new event added in between the remove_event phases.
        unpause_mock.assert_not_called()

    @mock_s3
    def test_remove_pipeline_event_missing_event_error(self):
        pause_ops = self._create_s3_pause_event_ops_obj()
        with self.assertRaises(PauseEventNotFound):
            pause_ops.remove_pipeline_event(self.TEST_USER, 'NOT_AN_EVENT_ID')

    @mock_s3
    def test_remove_pipeline_event_missing_historical_event_error(self):
        pause_ops = self._create_s3_pause_event_ops_obj()
        # Inject a pipeline pause event file, using boto calls - not the API.
        # pylint: disable=protected-access
        with freeze_time("2016-04-08 05:15:15"):
            event_id = 'FAKE_EVENT_ID'
            event_time_str = datetime.now().strftime(pause_ops.TIME_FORMAT)
            current_evt_no_history_filename = pause_ops._make_pause_event_filename(
                event_id,
                event_time_str,
                self.TEST_PIPELINE_SYSTEM
            )
        pause_ops._create_s3_file(
            '{}{}'.format(pause_ops.CURRENT_DIRECTORY, current_evt_no_history_filename),
            "{{time_paused: '{}', event_id: {}, pipeline_system: {}}}".format(
                event_time_str,
                event_id,
                self.TEST_PIPELINE_SYSTEM
            )
        )
        # Since the event file has no equivalent historical file, exception will be raised.
        with self.assertRaises(HistoricalEventNotFound):
            pause_ops.remove_pipeline_event(self.TEST_USER, event_id)

    @patch.object(GoCDAPI, 'pause_pipeline')
    @mock_s3
    def test_remove_pipeline_event_multiple_events_error(self, __):
        pause_ops = self._create_s3_pause_event_ops_obj()
        # Add a pipeline pause event.
        pause1_status = pause_ops.add_pipeline_event(
            self.TEST_USER,
            self.TEST_PIPELINE_SYSTEM,
            'Paused for a test reason.'
        )
        # Inject another pipeline pause event file, using boto calls - not the API.
        # pylint: disable=protected-access
        with freeze_time("2016-04-08 05:15:15"):
            duplicate_event_filename = pause_ops._make_pause_event_filename(
                pause1_status['event_id'],
                datetime.now().strftime(pause_ops.TIME_FORMAT),
                self.TEST_PIPELINE_SYSTEM
            )
        pause_ops._create_s3_file(
            '{}{}'.format(pause_ops.CURRENT_DIRECTORY, duplicate_event_filename),
            "{{event_id: {}, pipeline_system: {}}}".format(
                pause1_status['event_id'],
                self.TEST_PIPELINE_SYSTEM
            )
        )
        with self.assertRaises(MultiplePauseEventsFound):
            pause_ops.remove_pipeline_event(self.TEST_USER, pause1_status['event_id'])

    @mock_s3
    def test_pipeline_status_single_system_no_event(self):
        pause_ops = self._create_s3_pause_event_ops_obj()
        # Even if not paused, the asked-for system status is always returned.
        state = pause_ops.pipeline_status('edxapp', paused_only=True)
        self.assertDictEqual(state, {'edxapp': []})
        state = pause_ops.pipeline_status('edxapp', paused_only=False)
        self.assertDictEqual(state, {'edxapp': []})

    @patch.object(GoCDAPI, 'pause_pipeline')
    @mock_s3
    def test_pipeline_status_single_system_single_event(self, __):
        pause_ops = self._create_s3_pause_event_ops_obj()
        pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            'Paused for a test reason.'
        )
        state = pause_ops.pipeline_status('edxapp')
        self.assertEqual(len(state), 1)
        self.assertIn('edxapp', state)
        self.assertEqual(len(state['edxapp']), 1)

    @patch.object(GoCDAPI, 'pause_pipeline')
    @mock_s3
    def test_pipeline_status_single_system_multiple_events(self, __):
        pause_ops = self._create_s3_pause_event_ops_obj()
        pause1_status = pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            'Paused for a test reason.'
        )
        pause2_status = pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            'Paused for another test reason.'
        )
        pause3_status = pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            'Paused for yet another test reason.'
        )
        # Assert that three unique event IDs were generated.
        self.assertEqual(
            len(set([x['event_id'] for x in (pause1_status, pause2_status, pause3_status)])),
            3
        )
        state = pause_ops.pipeline_status('edxapp')
        self.assertEqual(len(state), 1)
        self.assertIn('edxapp', state)
        self.assertEqual(len(state['edxapp']), 3)

    @patch.object(GoCDAPI, 'pause_pipeline')
    @mock_s3
    def test_pipeline_status_single_system_multiple_events_remove_one(self, __):
        pause_ops = self._create_s3_pause_event_ops_obj()
        pause1_status = pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            'Paused for a test reason.'
        )
        pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            'Paused for another test reason.'
        )
        pause_ops.add_pipeline_event(
            self.TEST_USER,
            'edxapp',
            'Paused for yet another test reason.'
        )
        remove_status = pause_ops.remove_pipeline_event(self.TEST_USER, pause1_status['event_id'])
        self.assertEqual(remove_status['pipeline_system'], 'edxapp')
        self.assertFalse(remove_status['unpaused'])
        self.assertEqual(remove_status['num_remaining_events'], 2)

    @patch.object(GoCDAPI, 'pause_pipeline')
    @mock_s3
    def test_pipeline_status_multiple_systems(self, __):
        pause_ops = self._create_s3_pause_event_ops_obj()
        with patch.dict(
            'alton.pause_event.PIPELINE_SYSTEM_INFO',
            {'edxapp': [], 'ecommerce': []}
        ):
            pause_ops.add_pipeline_event(
                'user1',
                'edxapp',
                'Paused for a test reason.'
            )
            pause_ops.add_pipeline_event(
                'user2',
                'ecommerce',
                'Paused because a bug has been found.'
            )
            state = pause_ops.pipeline_status('edxapp', paused_only=True)
            self.assertEqual(len(state), 1)
            self.assertIn('edxapp', state)
            self.assertEqual(len(state['edxapp']), 1)
            state = pause_ops.pipeline_status()
            self.assertEqual(len(state), 2)
            self.assertIn('edxapp', state)
            self.assertEqual(len(state['edxapp']), 1)
            self.assertIn('ecommerce', state)
            self.assertEqual(len(state['ecommerce']), 1)

    @patch.object(GoCDAPI, 'pause_pipeline')
    @mock_s3
    def test_pipeline_status_multiple_systems_with_unpaused(self, __):
        pause_ops = self._create_s3_pause_event_ops_obj()
        with patch.dict(
            'alton.pause_event.PIPELINE_SYSTEM_INFO',
            {'edxapp': [], 'ecommerce': [], 'userauth': []}
        ):
            pause_ops.add_pipeline_event(
                'user1',
                'edxapp',
                'Paused for a test reason.'
            )
            pause_ops.add_pipeline_event(
                'user2',
                'ecommerce',
                'Paused because a bug has been found.'
            )
            state = pause_ops.pipeline_status(paused_only=True)
            self.assertEqual(len(state), 2)
            self.assertIn('edxapp', state)
            self.assertEqual(len(state['edxapp']), 1)
            self.assertIn('ecommerce', state)
            self.assertEqual(len(state['ecommerce']), 1)
            state = pause_ops.pipeline_status(paused_only=False)
            self.assertEqual(len(state), 3)
            self.assertIn('edxapp', state)
            self.assertEqual(len(state['edxapp']), 1)
            self.assertIn('ecommerce', state)
            self.assertEqual(len(state['ecommerce']), 1)
            self.assertIn('userauth', state)
            self.assertEqual(len(state['userauth']), 0)
