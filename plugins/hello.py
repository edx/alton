"""
Hello plugin
"""
from will.plugin import WillPlugin
from will.decorators import respond_to


class HelloPlugin(WillPlugin):
    """
    Hello plugin
    """
    @respond_to("^hello")
    def hello(self, message):
        """
        Say hello to everyone in the room.
        """
        self.reply(message, "hello everyone in {}!".format(self.get_room_from_message(message)['name']))

    @respond_to("^hi$")
    def hi_user(self, message):
        """
        Greet the user.
        """
        self.reply(message, "hi {}!".format(message.sender.nick))

    @respond_to("^ping$")
    def ping(self, message):
        """
        Reply with a pong.
        """
        self.reply(message, "PONG")

    @respond_to("^pong$")
    def pong(self, message):
        """
        Reply with a ping.
        """
        self.reply(message, "PING")

    @respond_to(r"^tell (?P<channel>\w+) (?P<what>.*)")
    def tell(self, message, channel, what):
        """
        Tell a room something.
        """
        self.reply(message, "OK!")
        self.say(what, room=self.get_room_from_name_or_id(channel))
