# To run these, go to the `plugins` directory and run `python -m unittest discover`

from show import *
from pyparsing import ParseException
import unittest

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
