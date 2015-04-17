from will.plugin import WillPlugin
from will.decorators import respond_to, periodic, hear, randomly, route, rendered_template


class NotifyPlugin(WillPlugin):

    @route("/notify/<build_id>/<text>")
    def send_notification(self, build_id, text):
        notification_list = self.load("notify_{}".format(build_id), '')
        self.say("{} BuildID: {}, Message: {}".format(notification_list, build_id, text)) 

