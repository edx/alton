# To run these, go to the `plugins` directory and run `python -m unittest discover`

from show import *
from pyparsing import ParseException
import unittest, mock

class TestParseCutAmi(unittest.TestCase):
    valid = [
        {'text': 'cut ami for one-two-three from foo-bar-baz',
         'msg' : 'basic syntax check failed'},
        {'text': 'cut ami for prod_a-edx_b-programs_c from stage_1-edx_2-programs_3',
         'msg' : 'some valid character(s) not allowed in e-d-c identifier'},
        {'text': 'cut ami for prod-edx-programs from stage-edx-programs using ami-deadbeef',
         'msg' : '"using" statement failed'},
        {'text': 'cut ami for prod-edx-edxapp from stage-edx-edxapp with configuration=master',
         'msg' : '"with" statement failed'},
        {'text': 'cut ami for foo-bar-baz from one-two-three with foo=bar bing=baz',
         'msg' : 'multiple overrides in "with" statement failed'},
        {'text': 'cut ami for foo-bar-baz from one-two-three with foo =bar bing= baz',
         'msg' : 'using spaces in "with" statement overrides should be permitted'},
        {'text': 'cut ami for prod-edx-programs from stage-edx-programs using ami-deadbeef with configuration=master configuration_secure=master programs_version=master',
         'msg' : '''using both "using" and "with" statements failed or they're not order-independent'''},
        {'text': 'cut ami for prod-edx-programs from stage-edx-programs with configuration=master configuration_secure=master programs_version=master using ami-deadbeef',
         'msg' : '''using both "using" and "with" statements failed or they're not order-independent'''},
        {'text': 'verbose cut ami for prod-edx-programs from stage-edx-programs',
         'msg' : '"verbose" option failed'},
        {'text': 'noop cut ami for prod-edx-programs from stage-edx-programs',
         'msg' : '"noop" option failed'},
        {'text': 'noop verbose cut ami for prod-edx-programs from stage-edx-programs',
         'msg' : '''using both "noop" and "verbose" options failed or they're not order-independent'''},
        {'text': 'verbose noop cut ami for prod-edx-programs from stage-edx-programs',
         'msg' : '''using both "noop" and "verbose" options failed or they're not order-independent'''},
    ]

    def test_valid(self):
        for test in self.valid:
            try:
                ShowPlugin._parse_cut_ami(test['text'])
            except ParseException as e:
                self.fail('Parsing failed for string: `{}` error: `{}` message: `{}`'.format(
                    test['text'], e.message, test['msg']))


    invalid = [
        {'text': 'cut ami for prod -edx-edxapp from stage-edx-  programs',
         'msg' : 'spaces should not be allowed in an e-d-p'},
        {'text': 'cut ami for foo-bar-baz from one-two-three using esdewfeokjsd',
         'msg' : 'invalid AMI string allowed (should be "ami-" + 8 hex chars)'},
        {'text': 'cut ami for foo-bar-baz from one-two-three using ami-jklmnopq',
         'msg' : 'invalid characters allowed in AMI ID (hex chars only)'},
        {'text': 'cut ami for foo-bar-baz from one-two-three using ami-deadbeef1',
         'msg' : 'allowed AMI ID that was too long (8 characters expected)'},
        {'text': 'cut ami for foo-bar-baz from one-two-three using ami-deadbee',
         'msg' : 'allowed AMI ID that was too short (8 characters expected)'},

        #These'll succeed until pyparsing 2.0.6 <http://sourceforge.net/p/pyparsing/code/296/>
        # {'text': 'cut ami for prod-edge-programs from stage-edx-programs using ami-deadbeef using ami-00000000',
        #  'msg' : 'allowed multiple "using" statments'},
        # {'text': 'cut ami for prod-edge-programs from prod-edge-programs with foo=bar with bing=baz',
        #  'msg' : 'allowed multiple "with" statements'},
    ]

    def test_invalid(self):
        for test in self.invalid:
            try:
                ShowPlugin._parse_cut_ami(test['text'])
                self.fail('Parsing erroneously succeeded for string `{}`: {}'.format(
                    test['text'], test['msg']))
            except:
                pass

    def test_basic_properties(self):
        text = "cut ami for foo-bar-baz from one-two-three"
        result = ShowPlugin._parse_cut_ami(text)
        self.assertEqual(result, {
            'dest_env':          'foo',
            'dest_dep':          'bar',
            'dest_play':         'baz',
            'source_env':        'one',
            'source_dep':        'two',
            'source_play':       'three',
            'base_ami':          None,
            'version_overrides': None,
            'verbose':           False,
            'noop':              False,
        })

    def test_all_properties(self):
        text = "verbose noop cut ami for foo-bar-baz from one-two-three using ami-deadbeef with thing=athing bang=abang"
        result = ShowPlugin._parse_cut_ami(text)
        self.assertEqual(result, {
            'dest_env':          'foo',
            'dest_dep':          'bar',
            'dest_play':         'baz',
            'source_env':        'one',
            'source_dep':        'two',
            'source_play':       'three',
            'base_ami':          'ami-deadbeef',
            'version_overrides': {'thing': 'athing', 'bang': 'abang'},
            'verbose':           True,
            'noop':              True,
        })


class TestCutFromEdp(unittest.TestCase):
    @mock.patch.object(ShowPlugin, '_get_ami_versions', return_value = Versions(    #uses boto
        'CONFIG REF', 'CONFIG_SECURE REF', {'thing': 'someotherthing', 'bang': 'someotherbang'},
        {'thing': {'url': 'THINGURL', 'shorthash': 'THINGSHORTHASH'},
         'bang':  {'url': 'BANGURL',  'shorthash': 'BANGSHORTHASH'}}
    ))
    @mock.patch.object(ShowPlugin, 'say')   #uses hipchat connection
    @mock.patch.object(ShowPlugin, '__init__', return_value=None)   #uses boto
    @mock.patch.object(ShowPlugin, '_ami_for_edp', return_value='ami-00000000') #uses boto
    @mock.patch.object(ShowPlugin, '_notify_abbey')     #this is how we test the result
    def test_result(self, mocked_notify_abbey, *args):
        message = mock.Mock()
        show_plugin = ShowPlugin()
        body = "verbose noop cut ami for foo-bar-baz from one-two-three using ami-deadbeef with thing=athing bang=abang"
        final_versions = Versions(
            'CONFIG REF', 'CONFIG_SECURE REF',
            {'THING': 'athing', 'thing': 'athing', 'BANG': 'abang', 'bang': 'abang'},
            {'thing': {'url': 'THINGURL', 'shorthash': 'THINGSHORTHASH'},
             'bang':  {'url': 'BANGURL',  'shorthash': 'BANGSHORTHASH'}}
        )

        show_plugin.cut_from_edp(message, body)
        #spec: self._notify_abbey(message, dest_env, dest_dep, dest_play,
        #    final_versions, noop, dest_running_ami, verbose)
        mocked_notify_abbey.assert_called_with(message, 'foo', 'bar', 'baz', mock.ANY, True, 'ami-deadbeef', True)

        called_versions = mocked_notify_abbey.call_args[0][4]
        self.assertEqual(
            [called_versions.configuration, called_versions.configuration_secure,
            called_versions.play_versions, called_versions.repos],
            [final_versions.configuration, final_versions.configuration_secure,
            final_versions.play_versions, final_versions.repos]
        )
