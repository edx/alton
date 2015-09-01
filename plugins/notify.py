from will.plugin import WillPlugin
from will.decorators import respond_to, periodic, hear, randomly, route, rendered_template


class NotifyPlugin(WillPlugin):

    @route("/notify/<build_id>/<text>")
    def send_notification(self, build_id, text):
        if '@' in build_id:
            # We're using this to handle the edge case of a notification list that's passed in through jenkins instead of registered in alton.
            notification_list = build_id
            self.say("{} Message: {}".format(notification_list, text), notify=True) 
        else:
            notification_list = self.load("notify_" + build_id, {})
            for room in notification_list:
                self.say("{} BuildID: {}, Message: {}".format(' '.join('@'+user for user in notification_list.get(room, [])), build_id, text), room=self.get_room_from_name_or_id(room), notify=True) 

    @respond_to("^subscribe (@?)(?P<users>(\S+ )+)to(?P<build_ids>( \S+)+)")
    def subscribe(self, message, users, build_ids):
        """
        subscribe [user user ...] to [buildid buildid ...]: request to be notified when builds complete
        """
        channel = self.get_room_from_message(message)['name']
        users = users.split()
        build_ids = {build_id: None for build_id in build_ids.split()}

        users = [message.sender.nick if user == "me" else user for user in users]

        for build_id in build_ids:
            notification_list = self.load("notify_" + build_id, None)
            if not notification_list:
                self.reply(message, "Sorry, I don't know about a token named {}".format(build_id), color='red')
                return
            else:
                build_ids[build_id] = notification_list

        for user in users:
            for build_id, notification_list in build_ids.iteritems():
                notification_list[channel] = notification_list.get(channel, [])
                notification_list[channel].append(user)
                notification_list[channel] = list(set(notification_list.get(channel, [])))
                build_ids[build_id] = notification_list
                self.save('notify_' + build_id, notification_list, expire=259200)

        for build_id in build_ids:
            self.reply(message, "OK, I'll tell {} when I hear about {}".format(
                ', '.join(user for user in notification_list.get(channel, [])), 
                build_id))

    @respond_to("^who is subscribed to (?P<build_id>\S+)")
    def check_subscribe(self, message, build_id):
        """
        who is subscribed to [buildid]: see the notification list for a token
        """
        notification_list = self.load("notify_" + build_id, {})
        self.reply(message, "Subscription list:")
        for room in notification_list:
            self.reply(message, "{}:  {}".format(room, ', '.join(notification_list.get(room, []))))



