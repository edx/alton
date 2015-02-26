from will.plugin import WillPlugin
from will.decorators import respond_to

class UserPlugin(WillPlugin):
    def __check_user_permission(username, permission):
        user_permissions = self.load("user_permissions", {})
        try:
            if permission in user_permissions[username]:
                return 1
            else:
                return 0
        except KeyError:
            return 0

    @respond_to("^what can (?P<username>\w*) do")
    def show_user_permission(self, message, username):
        if 'I' == username:
            username = message.sender.nick
        user_permissions = self.load("user_permissions", {})
        try:
            self.say("{}'s permissions are: {}".format(username, ', '.join(user_permissions[username])), message=message) 
        except KeyError:
            self.say("I don't know who {} is. (no permissions)".format(username), message=message)

    @respond_to("^can I(?P<permissions>( \w*)*)")
    def confirm_user_permission(self, message, permissions):
        for permission in permissions.split():
            user_permissions = self.load("user_permissions", {})
            try:
                if permission in user_permissions[message.sender.nick]:
                    self.say("you can {}".format(permission))
                else:
                    self.say("you can't {}".format(permission))
            except KeyError:
                self.say("you can't {}".format(permission))



    @respond_to("^give (?P<username>\w*) permission(?P<permissions>( \w*)*)")
    def give_user_permission(self, message, username, permissions):
        requested_permissions = permissions.split()
        self.say("adding permissions : {} ".format(permissions), message=message) 
        try:
            user_permissions = self.load("user_permissions", {})
            new_permissions = list(set(requested_permissions + user_permissions[username]))
        except KeyError:
            new_permissions = requested_permissions
        user_permissions[username] = new_permissions
        self.save("user_permissions", user_permissions)
        self.say("new permissions are: {} ".format(', '.join(new_permissions)), message=message) 

