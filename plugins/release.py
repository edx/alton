"""
Commands to pause/unpause release pipeline systems.
"""
import pprint
import logging

from will import settings
from will.plugin import WillPlugin
from will.decorators import respond_to

from alton.pause_event import (
    PIPELINE_SYSTEM_INFO,
    S3PauseEventOps,
    PauseEventNotFound,
    MultiplePauseEventsFound
)

log = logging.getLogger(__name__)

# pylint: disable=len-as-condition


class ReleasePlugin(WillPlugin):
    """
    Plugin containing commands to pause/unpause release pipeline systems.
    """
    def __init__(self):
        for required_var in ['PIPELINE_BUCKET_NAME', 'GOCD_USERNAME', 'GOCD_PASSWORD', 'GOCD_SERVER_URL']:
            if not hasattr(settings, required_var):
                msg = "Error: {} not defined in the environment".format(required_var)
                self._say_error(msg)
        # pylint: disable=no-member
        self.pause_ops = S3PauseEventOps(
            settings.PIPELINE_BUCKET_NAME,
            settings.GOCD_USERNAME,
            settings.GOCD_PASSWORD,
            settings.GOCD_SERVER_URL
        )

    def _say(self, msg, message=None):
        """
        Formats responses as code and says them back to HipChat.
        """
        self.say('/code {}'.format(msg), message=message, color="green")

    def _say_error(self, msg, message=None):
        """
        Reports an error
        """
        self.say(msg, message=message, color="red")

    def _check_pipeline_system(self, pipeline_system, message):
        """
        Checks a passed-in pipeline system to ensure it's a known system.
        If so, returns True.
        If unknown, tells the user and returns False.
        """
        if pipeline_system and pipeline_system not in PIPELINE_SYSTEM_INFO:
            self._say_error(
                "Pipeline system '{}' is unknown. Known systems: {}".format(
                    pipeline_system, ', '.join(PIPELINE_SYSTEM_INFO.keys())
                ),
                message
            )
            return False
        return True

    def _format_status_output(self, pipeline_system, statuses, paused_only=False):
        """
        Takes the pipeline pause event output read from S3 and formats it into a string to return.
        """
        if pipeline_system:
            # Output for a single pipeline system.
            cmd_output = "Pipeline system: {}\n".format(pipeline_system)
            if len(statuses[pipeline_system]) > 0:
                cmd_output += "    PAUSED for {} reason(s)\n".format(len(statuses[pipeline_system]))
                for pause_event in statuses[pipeline_system]:
                    cmd_output += pprint.pformat(pause_event, indent=4) + '\n'
            else:
                cmd_output += "    ACTIVE"

        else:
            # Output for all known pipeline systems.
            cmd_output = "Pipeline systems:\n"
            if len(statuses) > 0:
                for system, status in statuses.items():
                    # If only want to see paused systems -and- the system is active, skip.
                    if paused_only and len(status) == 0:
                        continue
                    if len(status) > 0:
                        status_str = "PAUSED for {} reason(s)".format(len(status))
                    else:
                        status_str = "ACTIVE"
                    cmd_output += "{:>10}: {}\n".format(system, status_str)
            else:
                # No statuses - must mean that no paused pipeline systems exist.
                cmd_output += "     All systems ({}) active.".format(', '.join(PIPELINE_SYSTEM_INFO.keys()))
        return cmd_output

    @respond_to(r"^pipeline[\s]+pause[\s]+"
                r"(?P<pipeline_system>\w*)[\s]+"  # Pipeline system to pause
                r"because[\s]+"
                r"(?P<pause_reason>.*)")  # Reason for pausing
    def pause(self, message, pipeline_system, pause_reason):
        """
        pipeline pause [pipeline_system] because [reason]
            : Pause the specified pipeline system for the specified reason.
        """
        if not self._check_pipeline_system(pipeline_system, message):
            return

        pause_status = self.pause_ops.add_pipeline_event(message.sender.nick, pipeline_system, pause_reason)
        response = "Added pause event {} for system '{}' - paused the system.".format(
            pause_status['event_id'],
            pipeline_system
        )
        self._say(response, message)

    @respond_to(r"^pipeline[\s]+resolve[\s]+"
                r"(?P<event_id>\w*)")  # Pipeline event to remove
    def remove_event(self, message, event_id):
        """
        pipeline resolve [event_id]
            : Remove the specified pipeline pause event.
        """
        try:
            remove_status = self.pause_ops.remove_pipeline_event(message.sender.nick, event_id)
        except PauseEventNotFound:
            self._say_error("Event '{}' was not found.".format(event_id), message)
        except MultiplePauseEventsFound:
            self._say_error(
                "Multiple events found with ID '{}'? Should not happen - check the S3 bucket.".format(event_id),
                message
            )
        else:
            response = "Event '{}' successfully removed from system '{}'".format(
                event_id,
                remove_status['pipeline_system']
            )
            if remove_status['unpaused']:
                response += " - unpaused the system."
            else:
                response += " - {} pause event(s) remaining.".format(remove_status['num_remaining_events'])
            self._say(response, message)

    @respond_to(r"^pipeline[\s]+status[\s]*"
                r"(?P<pipeline_system>\w*|)")  # Pipeline system for which to retrieve status.
    def status(self, message, pipeline_system):  # pylint: disable=unused-argument
        """
        pipeline status [pipeline_system]
            : Returns the status of one or all pipeline systems.
        """
        if not self._check_pipeline_system(pipeline_system, message):
            return

        statuses = self.pause_ops.pipeline_status(pipeline_system)
        cmd_output = self._format_status_output(pipeline_system, statuses)
        self._say(cmd_output, message)
