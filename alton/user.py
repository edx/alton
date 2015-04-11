from will import settings
from will.decorators import rendered_template
import pyotp
import qrcode
import base64
import boto
import boto.s3.connection
from boto.s3.key import Key
import cStringIO
import datetime
import urlparse
from simplecrypt import encrypt, decrypt
import pytz


def requires_permission(permission):
    """
    A decorator that ensures the user sending the message is authenticated and authorized to perform the action.

    Example:

        @respond_to(r'^hi')
        @requires_permission('greet_users')
        def say_hello(self, message):
            self.reply(message, "Hello!")
    """
    def decorator_check_permission(func):
        def process_response(plugin, message, *args, **kwargs):
            if check_permission(plugin, message, permission):
                return func(plugin, message, *args, **kwargs)

        return process_response

    return decorator_check_permission


def check_permission(plugin, message, permission):
    """
    Ensure the user sending the message is authenticated and authorized to perform the action.

    This can be used to check permissions that are scoped to a particular entity. For example you might grant a
    permission like "deploy:staging" which allows users to deploy to the staging environment.

    Example:

        @respond_to('^deploy to (?P<environment>\w+)')
        def deploy(self, message, environment):
            if not check_permission(self, message, 'deploy:' + environment):
                self.reply(message, 'Sorry, you are not allowed to deploy to "{}"'.format(environment))
                return
            else:
                real_deploy(environment)
    """
    requestor = User.get_from_message(plugin, message)
    if not requestor:
        return False

    if not requestor.is_authenticated:
        plugin.send_direct_message(message.sender["hipchat_id"], "You are not authenticated, please verify your identity by saying 'twofactor verify [token]' and try again")
        return False

    if not requestor.has_permission(permission):
        plugin.send_direct_message(message.sender["hipchat_id"], "You are not authorized to perform this action")
        return False
    else:
        return True


class User(object):
    """
    Represents a user.

    Contains sufficient information to determine if they are authenticated and what permissions they have.

    Constructor Arguments:

        plugin: The plugin that is currently responding to a message.
        user_metadata: A dictionary that contains a "nick" key and a "hipchat_id". This dictionary is used by will to
            represent a user quite frequently.
        token: The secret base32 TOTP token used to verify transient tokens provided by external devices. This is as
            sensitive as a user's password and should be handled with care.
        permissions: A set() containing strings that represent all permissions the user has been granted.
        verified_time: The last time the user verified their identity as a datetime object with tzinfo == UTC.
    """

    # Design notes

    # All of the data about that user that needs to be persisted is stored in a dictionary in the key-value store. Each
    # user has their own key. Since the bot may be responding to several requests at once on different threads, this
    # seemed safer than having one massive dictionary storing all user information in it (fewer race conditions). That
    # said - some race conditions to exists, particularly around permission management. If two admins decide to modify
    # permissions of a particular user at roughly the same time, then one of them will overwrite the other which may
    # not be the desired effect. This could be mitigated by taking advantage of some of Redis's built in "set"
    # management features.

    # From a performance standpoint, some calls here require calls to the hipchat API. I know (for example) that
    # retrieving the user's timezone requires one such call. I suspect others do as well, however, the calls are made
    # deep inside Will, so it's not abundantly clear which function calls result in API calls and which ones don't.
    # If there were performance problems, or we start hitting rate limits on the hipchat API we could cache stuff like
    # the timezone in Redis, and just assume it's very "slow changing".

    SESSION_DURATION = getattr(settings, 'TWOFACTOR_SESSION_DURATION', 30 * 60)  # 30 minutes in seconds
    QR_CODE_VALIDITY_DURATION = getattr(settings, 'TWOFACTOR_QR_CODE_VALIDITY_DURATION', 10 * 60)  # 10 minutes in seconds

    def __init__(self, plugin, user_metadata, token, permissions, verified_time=None):
        self.nick = user_metadata['nick']
        self.hipchat_id = user_metadata['hipchat_id']
        self.totp = pyotp.TOTP(token)
        self.token = token
        self.permissions = permissions
        self.plugin = plugin
        self.verified_time = verified_time

    def verify_token(self, entered_token):
        """
        Return True iff the entered_token is valid given the TOTP secret for this user.

        Side effects: if the token is valid then self.verified_time is updated to the current time.
        """
        is_valid = self.totp.verify(entered_token)
        if is_valid:
            self.verified_time = datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
        return is_valid

    @property
    def is_authenticated(self):
        """True iff the user has successfully authenticated recently enough that their session hasn't expired"""
        if self.verified_time is None:
            return False

        now = datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
        seconds_since_last_verify = (now - self.verified_time).total_seconds()
        return seconds_since_last_verify < self.SESSION_DURATION

    @property
    def timezone(self):
        """The user's timezone as a pytz.timezone()"""
        if not hasattr(self, '_timezone'):
            full_user_info = self.plugin.get_hipchat_user(self.hipchat_id)
            self._timezone = pytz.timezone(full_user_info['timezone'])

        return self._timezone

    def localize_time(self, dt):
        """Given a datetime object, localize it in the user's timezone"""
        return dt.astimezone(self.timezone)

    @property
    def session_expiration_time(self):
        """The localized datetime that the user's session expires or None"""
        if self.verified_time is None:
            return None

        return self.localize_time(self.verified_time + datetime.timedelta(seconds=self.SESSION_DURATION))

    def logout(self):
        """
        Ensure that the user has to authenticate again once this action completes if they wish to continue the session.
        """
        self.verified_time = None

    def save(self):
        """
        Persist the model in the plugin storage.

        Note this encrypts the secure token before storing it externally.
        """
        as_dict = {
            'token': encrypt(settings.TWOFACTOR_SECRET, self.token),
            'permissions': self.permissions,
        }
        if self.verified_time:
            as_dict['verified_time'] = self.verified_time
        self.plugin.save(User.get_key(self.hipchat_id), as_dict)

    def delete(self):
        """Remove the user"""
        self.plugin.clear(User.get_key(self.hipchat_id))

    def generate_and_upload_qr_code_image(self):
        """
        Generate a QR code that can be used to configure Google Authenticator and return a link to a page that displays it.

        This URL can be accessed for by anyone who has it (for a period of time), so it should be handled with care.
        """
        s3_twofactor_bucket = settings.TWOFACTOR_S3_BUCKET
        s3_twofactor_path_prefix = ''
        parsed_url = urlparse.urlparse(s3_twofactor_bucket)
        if parsed_url.scheme == 's3':
            s3_twofactor_path_prefix = parsed_url.path
            s3_twofactor_bucket = parsed_url.netloc

        url = self.totp.provisioning_uri(self.nick, issuer_name=getattr(settings, 'TWOFACTOR_ISSUER', 'Alton'))
        img = qrcode.make(url)
        conn = boto.connect_s3(profile_name=getattr(settings, 'TWOFACTOR_S3_PROFILE', None))
        bucket = conn.get_bucket(s3_twofactor_bucket)
        key = Key(bucket)
        key.content_type = 'text/html'
        key.key = '{path}{name}-qr'.format(
            path=s3_twofactor_path_prefix,
            name=base64.urlsafe_b64encode(self.hipchat_id)
        )
        image_buffer = cStringIO.StringIO()
        img.save(image_buffer, 'PNG')
        image_data = image_buffer.getvalue().encode('base64').replace('\n', '')
        js_compatible_utc_timestamp = (datetime.datetime.utcnow() + datetime.timedelta(seconds=self.QR_CODE_VALIDITY_DURATION)).isoformat().split('.')[0]
        context = {
            'image_data': image_data,
            'secret': self.token,
            'code_expiration_timestamp': js_compatible_utc_timestamp,
        }
        rendered_html = rendered_template('qr_code.html', context)
        key.set_contents_from_string(rendered_html, encrypt_key=True)
        url = key.generate_url(self.QR_CODE_VALIDITY_DURATION, query_auth=True)
        return 'This link is valid for {} minutes: <a href="{}">View QR code</a>'.format(self.QR_CODE_VALIDITY_DURATION / 60, url)

    def has_permission(self, permission):
        """True iff the user is authenticated and has the requested permission."""
        if not self.is_authenticated:
            return False
        return permission in self.permissions

    def grant_permissions(self, permissions):
        """Give the user new permissions"""
        self.permissions.update(permissions)

    def revoke_permissions(self, permissions):
        """Take away some permissions from the user"""
        self.permissions.difference_update(permissions)

    @staticmethod
    def get(plugin, user_metadata):
        """Retrieve a user given a user_metadata dictionary, returns None if the user has never setup two-factor authentication"""
        if user_metadata is None:
            return None

        hipchat_id = user_metadata['hipchat_id']
        as_dict = plugin.load(User.get_key(hipchat_id))
        if as_dict:
            decrypted_token = decrypt(settings.TWOFACTOR_SECRET, as_dict['token'])
            return User(plugin, user_metadata, decrypted_token, as_dict['permissions'], as_dict.get('verified_time'))
        else:
            return None

    @staticmethod
    def get_from_nick(plugin, nick):
        """Retrieve a user given a nick, returns None if the user has never setup two-factor authentication"""
        user_metadata = None
        for jid, info in plugin.internal_roster.items():
            if info["nick"] == nick:
                user_metadata = info
                break

        return User.get(plugin, user_metadata)

    @staticmethod
    def get_from_message(plugin, message):
        """Retrieve a user given a message sent by the user, returns None if the user has never setup two-factor authentication"""
        user_metadata = plugin.get_user_from_message(message)
        user = User.get(plugin, user_metadata)
        if not user:
            plugin.send_direct_message(message.sender["hipchat_id"], "You have not setup authentication, please say 'twofactor me' to start setup")
        return user

    @staticmethod
    def create(plugin, user_metadata):
        """Creates a new user given the user_metadata dictionary, assigns a new random secret"""
        user = User.get(plugin, user_metadata)
        if not user:
            token = pyotp.random_base32()
            permissions = set()
            if user_metadata['nick'] in getattr(settings, 'ADMIN_USERS', '').split(','):
                permissions = set(['administer_twofactor', 'grant_permissions', 'revoke_permissions', 'view_permissions'])
            return User(plugin, user_metadata, token, permissions)
        else:
            return None

    @staticmethod
    def list(plugin):
        """Get a list of all users"""
        users_by_id = {}
        for jid, info in plugin.internal_roster.items():
            users_by_id[info['hipchat_id']] = info

        users = []
        for key in plugin.storage.keys('twofactor:user:*'):
            hipchat_id = key.split(':')[2]
            user = User.get(plugin, users_by_id[hipchat_id])
            if user:
                users.append(user)

        return users

    @staticmethod
    def get_key(hipchat_id):
        """The key used to store the user in the plugin storage system"""
        return 'twofactor:user:{}'.format(hipchat_id)
