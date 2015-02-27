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
            self.say("@{}: {}'s permissions are: {}".format(message.sender.nick, username, ', '.join(user_permissions[username])), message=message) 
        except KeyError:
            self.say("@{}: I don't know who {} is. (no permissions)".format(message.sender.nick, username), message=message)


    @respond_to("^who can(?P<permissions>( \w*)*)")
    def find_user_permission(self, message, permissions):
        user_permissions = self.load("user_permissions", {})
        for permission in permissions.split():
            userlist = []
            for user in user_permissions:
                if permission in user_permissions[user]:
                    userlist.append(user)
                else:
                    pass
            self.say("@{}: {} can {}".format(message.sender.nick, ', '.join(userlist), permission), message=message)

    @respond_to("^can I(?P<permissions>( \w*)*)")
    def confirm_user_permission(self, message, permissions):
        user_permissions = self.load("user_permissions", {})
        for permission in permissions.split():
            try:
                if permission in user_permissions[message.sender.nick]:
                    self.say("@{}: Yes, you can {}".format(message.sender.nick, permission), message=message)
                else:
                    self.say("@{}: No, you can't {}".format(message.sender.nick, permission), message=message)
            except KeyError:
                self.say("@{}: No, you can't {}".format(message.sender.nick, permission), message=message)



    @respond_to("^give (?P<username>\w*) permission(?P<permissions>( \w*)*)")
    def give_user_permission(self, message, username, permissions):
        requested_permissions = permissions.split()
        try:
            requested_permissions.remove("to")
        except ValueError:
            pass
        #self.say("adding permissions : {} ".format(', '.join(requested_permissions)), message=message) 
        try:
            user_permissions = self.load("user_permissions", {})
            new_permissions = list(set(requested_permissions + user_permissions[username]))
        except KeyError:
            new_permissions = requested_permissions
        user_permissions[username] = new_permissions
        self.save("user_permissions", user_permissions)
        self.say("@{}: new permissions for {} are: {} ".format(message.sender.nick, username, ', '.join(new_permissions)), message=message) 

    @respond_to("^take away from (?P<username>\w*) permission(?P<permissions>( \w*)*)")
    def remove_user_permission(self, message, username, permissions):
        requested_permissions = permissions.split()
        requested_permissions.remove("to")
        #self.say("removing permissions : {} ".format(', '.join(requested_permissions)), message=message) 
        try:
            user_permissions = self.load("user_permissions", {})
            for permission in requested_permissions:
                try:
                    user_permissions[username].remove(permission)
                except ValueError:
                    pass
        except KeyError:
            pass
        self.save("user_permissions", user_permissions)
        self.say("@{}: new permissions for {} are: {} ".format(message.sender.nick, username, ', '.join(user_permissions[username])), message=message) 

    @respond_to("^trash all permissions")
    def delete_all_permission(self, message):
        self.save("user_permissions", {})
