import os
import struct
import unittest

from cloudinit.sources.helpers import azure as azure_helper
from ..helpers import TestCase

try:
    from unittest import mock
except ImportError:
    import mock

try:
    from contextlib import ExitStack
except ImportError:
    from contextlib2 import ExitStack


GOAL_STATE_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<GoalState xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="goalstate10.xsd">
  <Version>2012-11-30</Version>
  <Incarnation>{incarnation}</Incarnation>
  <Machine>
    <ExpectedState>Started</ExpectedState>
    <StopRolesDeadlineHint>300000</StopRolesDeadlineHint>
    <LBProbePorts>
      <Port>16001</Port>
    </LBProbePorts>
    <ExpectHealthReport>FALSE</ExpectHealthReport>
  </Machine>
  <Container>
    <ContainerId>{container_id}</ContainerId>
    <RoleInstanceList>
      <RoleInstance>
        <InstanceId>{instance_id}</InstanceId>
        <State>Started</State>
        <Configuration>
          <HostingEnvironmentConfig>http://100.86.192.70:80/machine/46504ebc-f968-4f23-b9aa-cd2b3e4d470c/68ce47b32ea94952be7b20951c383628.utl%2Dtrusty%2D%2D292258?comp=config&amp;type=hostingEnvironmentConfig&amp;incarnation=1</HostingEnvironmentConfig>
          <SharedConfig>{shared_config_url}</SharedConfig>
          <ExtensionsConfig>http://100.86.192.70:80/machine/46504ebc-f968-4f23-b9aa-cd2b3e4d470c/68ce47b32ea94952be7b20951c383628.utl%2Dtrusty%2D%2D292258?comp=config&amp;type=extensionsConfig&amp;incarnation=1</ExtensionsConfig>
          <FullConfig>http://100.86.192.70:80/machine/46504ebc-f968-4f23-b9aa-cd2b3e4d470c/68ce47b32ea94952be7b20951c383628.utl%2Dtrusty%2D%2D292258?comp=config&amp;type=fullConfig&amp;incarnation=1</FullConfig>
          <Certificates>{certificates_url}</Certificates>
          <ConfigName>68ce47b32ea94952be7b20951c383628.0.68ce47b32ea94952be7b20951c383628.0.utl-trusty--292258.1.xml</ConfigName>
        </Configuration>
      </RoleInstance>
    </RoleInstanceList>
  </Container>
</GoalState>
"""


class TestReadAzureSharedConfig(unittest.TestCase):

    def test_valid_content(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
            <SharedConfig>
             <Deployment name="MY_INSTANCE_ID">
              <Service name="myservice"/>
              <ServiceInstance name="INSTANCE_ID.0" guid="{abcd-uuid}" />
             </Deployment>
            <Incarnation number="1"/>
            </SharedConfig>"""
        ret = azure_helper.iid_from_shared_config_content(xml)
        self.assertEqual("MY_INSTANCE_ID", ret)


class TestFindEndpoint(TestCase):

    def setUp(self):
        super(TestFindEndpoint, self).setUp()
        patches = ExitStack()
        self.addCleanup(patches.close)

        self.load_file = patches.enter_context(
            mock.patch.object(azure_helper.util, 'load_file'))

    def test_missing_file(self):
        self.load_file.side_effect = IOError
        self.assertRaises(IOError,
                          azure_helper.WALinuxAgentShim.find_endpoint)

    def test_missing_special_azure_line(self):
        self.load_file.return_value = ''
        self.assertRaises(Exception,
                          azure_helper.WALinuxAgentShim.find_endpoint)

    def _build_lease_content(self, ip_address, use_hex=True):
        ip_address_repr = ':'.join(
            [hex(int(part)).replace('0x', '')
             for part in ip_address.split('.')])
        if not use_hex:
            ip_address_repr = struct.pack(
                '>L', int(ip_address_repr.replace(':', ''), 16))
            ip_address_repr = '"{0}"'.format(ip_address_repr.decode('utf-8'))
        return '\n'.join([
            'lease {',
            ' interface "eth0";',
            ' option unknown-245 {0};'.format(ip_address_repr),
            '}'])

    def test_hex_string(self):
        ip_address = '98.76.54.32'
        file_content = self._build_lease_content(ip_address)
        self.load_file.return_value = file_content
        self.assertEqual(ip_address,
                         azure_helper.WALinuxAgentShim.find_endpoint())

    def test_hex_string_with_single_character_part(self):
        ip_address = '4.3.2.1'
        file_content = self._build_lease_content(ip_address)
        self.load_file.return_value = file_content
        self.assertEqual(ip_address,
                         azure_helper.WALinuxAgentShim.find_endpoint())

    def test_packed_string(self):
        ip_address = '98.76.54.32'
        file_content = self._build_lease_content(ip_address, use_hex=False)
        self.load_file.return_value = file_content
        self.assertEqual(ip_address,
                         azure_helper.WALinuxAgentShim.find_endpoint())

    def test_latest_lease_used(self):
        ip_addresses = ['4.3.2.1', '98.76.54.32']
        file_content = '\n'.join([self._build_lease_content(ip_address)
                                  for ip_address in ip_addresses])
        self.load_file.return_value = file_content
        self.assertEqual(ip_addresses[-1],
                         azure_helper.WALinuxAgentShim.find_endpoint())


class TestGoalStateParsing(TestCase):

    default_parameters = {
        'incarnation': 1,
        'container_id': 'MyContainerId',
        'instance_id': 'MyInstanceId',
        'shared_config_url': 'MySharedConfigUrl',
        'certificates_url': 'MyCertificatesUrl',
    }

    def _get_goal_state(self, http_client=None, **kwargs):
        if http_client is None:
            http_client = mock.MagicMock()
        parameters = self.default_parameters.copy()
        parameters.update(kwargs)
        xml = GOAL_STATE_TEMPLATE.format(**parameters)
        if parameters['certificates_url'] is None:
            new_xml_lines = []
            for line in xml.splitlines():
                if 'Certificates' in line:
                    continue
                new_xml_lines.append(line)
            xml = '\n'.join(new_xml_lines)
        return azure_helper.GoalState(xml, http_client)

    def test_incarnation_parsed_correctly(self):
        incarnation = '123'
        goal_state = self._get_goal_state(incarnation=incarnation)
        self.assertEqual(incarnation, goal_state.incarnation)

    def test_container_id_parsed_correctly(self):
        container_id = 'TestContainerId'
        goal_state = self._get_goal_state(container_id=container_id)
        self.assertEqual(container_id, goal_state.container_id)

    def test_instance_id_parsed_correctly(self):
        instance_id = 'TestInstanceId'
        goal_state = self._get_goal_state(instance_id=instance_id)
        self.assertEqual(instance_id, goal_state.instance_id)

    def test_shared_config_xml_parsed_and_fetched_correctly(self):
        http_client = mock.MagicMock()
        shared_config_url = 'TestSharedConfigUrl'
        goal_state = self._get_goal_state(
            http_client=http_client, shared_config_url=shared_config_url)
        shared_config_xml = goal_state.shared_config_xml
        self.assertEqual(1, http_client.get.call_count)
        self.assertEqual(shared_config_url, http_client.get.call_args[0][0])
        self.assertEqual(http_client.get.return_value.contents,
                         shared_config_xml)

    def test_certificates_xml_parsed_and_fetched_correctly(self):
        http_client = mock.MagicMock()
        certificates_url = 'TestSharedConfigUrl'
        goal_state = self._get_goal_state(
            http_client=http_client, certificates_url=certificates_url)
        certificates_xml = goal_state.certificates_xml
        self.assertEqual(1, http_client.get.call_count)
        self.assertEqual(certificates_url, http_client.get.call_args[0][0])
        self.assertTrue(http_client.get.call_args[1].get('secure', False))
        self.assertEqual(http_client.get.return_value.contents,
                         certificates_xml)

    def test_missing_certificates_skips_http_get(self):
        http_client = mock.MagicMock()
        goal_state = self._get_goal_state(
            http_client=http_client, certificates_url=None)
        certificates_xml = goal_state.certificates_xml
        self.assertEqual(0, http_client.get.call_count)
        self.assertIsNone(certificates_xml)


class TestAzureEndpointHttpClient(TestCase):

    regular_headers = {
        'x-ms-agent-name': 'WALinuxAgent',
        'x-ms-version': '2012-11-30',
    }

    def setUp(self):
        super(TestAzureEndpointHttpClient, self).setUp()
        patches = ExitStack()
        self.addCleanup(patches.close)

        self.read_file_or_url = patches.enter_context(
            mock.patch.object(azure_helper.util, 'read_file_or_url'))

    def test_non_secure_get(self):
        client = azure_helper.AzureEndpointHttpClient(mock.MagicMock())
        url = 'MyTestUrl'
        response = client.get(url, secure=False)
        self.assertEqual(1, self.read_file_or_url.call_count)
        self.assertEqual(self.read_file_or_url.return_value, response)
        self.assertEqual(mock.call(url, headers=self.regular_headers),
                         self.read_file_or_url.call_args)

    def test_secure_get(self):
        url = 'MyTestUrl'
        certificate = mock.MagicMock()
        expected_headers = self.regular_headers.copy()
        expected_headers.update({
            "x-ms-cipher-name": "DES_EDE3_CBC",
            "x-ms-guest-agent-public-x509-cert": certificate,
        })
        client = azure_helper.AzureEndpointHttpClient(certificate)
        response = client.get(url, secure=True)
        self.assertEqual(1, self.read_file_or_url.call_count)
        self.assertEqual(self.read_file_or_url.return_value, response)
        self.assertEqual(mock.call(url, headers=expected_headers),
                         self.read_file_or_url.call_args)

    def test_post(self):
        data = mock.MagicMock()
        url = 'MyTestUrl'
        client = azure_helper.AzureEndpointHttpClient(mock.MagicMock())
        response = client.post(url, data=data)
        self.assertEqual(1, self.read_file_or_url.call_count)
        self.assertEqual(self.read_file_or_url.return_value, response)
        self.assertEqual(
            mock.call(url, data=data, headers=self.regular_headers),
            self.read_file_or_url.call_args)

    def test_post_with_extra_headers(self):
        url = 'MyTestUrl'
        client = azure_helper.AzureEndpointHttpClient(mock.MagicMock())
        extra_headers = {'test': 'header'}
        client.post(url, extra_headers=extra_headers)
        self.assertEqual(1, self.read_file_or_url.call_count)
        expected_headers = self.regular_headers.copy()
        expected_headers.update(extra_headers)
        self.assertEqual(
            mock.call(mock.ANY, data=mock.ANY, headers=expected_headers),
            self.read_file_or_url.call_args)


class TestOpenSSLManager(TestCase):

    def setUp(self):
        super(TestOpenSSLManager, self).setUp()
        patches = ExitStack()
        self.addCleanup(patches.close)

        self.subp = patches.enter_context(
            mock.patch.object(azure_helper.util, 'subp'))

    @mock.patch.object(azure_helper, 'cd', mock.MagicMock())
    @mock.patch.object(azure_helper.tempfile, 'TemporaryDirectory')
    def test_openssl_manager_creates_a_tmpdir(self, TemporaryDirectory):
        manager = azure_helper.OpenSSLManager()
        self.assertEqual(TemporaryDirectory.return_value, manager.tmpdir)

    @mock.patch('builtins.open')
    def test_generate_certificate_uses_tmpdir(self, open):
        subp_directory = {}

        def capture_directory(*args, **kwargs):
            subp_directory['path'] = os.getcwd()

        self.subp.side_effect = capture_directory
        manager = azure_helper.OpenSSLManager()
        self.assertEqual(manager.tmpdir.name, subp_directory['path'])


class TestWALinuxAgentShim(TestCase):

    def setUp(self):
        super(TestWALinuxAgentShim, self).setUp()
        patches = ExitStack()
        self.addCleanup(patches.close)

        self.AzureEndpointHttpClient = patches.enter_context(
            mock.patch.object(azure_helper, 'AzureEndpointHttpClient'))
        self.find_endpoint = patches.enter_context(
            mock.patch.object(
                azure_helper.WALinuxAgentShim, 'find_endpoint'))
        self.GoalState = patches.enter_context(
            mock.patch.object(azure_helper, 'GoalState'))
        self.iid_from_shared_config_content = patches.enter_context(
            mock.patch.object(azure_helper, 'iid_from_shared_config_content'))
        self.OpenSSLManager = patches.enter_context(
            mock.patch.object(azure_helper, 'OpenSSLManager'))

    def test_http_client_uses_certificate(self):
        shim = azure_helper.WALinuxAgentShim()
        self.assertEqual(
            [mock.call(self.OpenSSLManager.return_value.certificate)],
            self.AzureEndpointHttpClient.call_args_list)
        self.assertEqual(self.AzureEndpointHttpClient.return_value,
                         shim.http_client)

    def test_correct_url_used_for_goalstate(self):
        self.find_endpoint.return_value = 'test_endpoint'
        shim = azure_helper.WALinuxAgentShim()
        shim.register_with_azure_and_fetch_data()
        get = self.AzureEndpointHttpClient.return_value.get
        self.assertEqual(
            [mock.call('http://test_endpoint/machine/?comp=goalstate')],
            get.call_args_list)
        self.assertEqual(
            [mock.call(get.return_value.contents, shim.http_client)],
            self.GoalState.call_args_list)

    def test_certificates_used_to_determine_public_keys(self):
        shim = azure_helper.WALinuxAgentShim()
        data = shim.register_with_azure_and_fetch_data()
        self.assertEqual(
            [mock.call(self.GoalState.return_value.certificates_xml)],
            self.OpenSSLManager.return_value.parse_certificates.call_args_list)
        self.assertEqual(
            self.OpenSSLManager.return_value.parse_certificates.return_value,
            data['public-keys'])

    def test_absent_certificates_produces_empty_public_keys(self):
        self.GoalState.return_value.certificates_xml = None
        shim = azure_helper.WALinuxAgentShim()
        data = shim.register_with_azure_and_fetch_data()
        self.assertEqual([], data['public-keys'])

    def test_instance_id_returned_in_data(self):
        shim = azure_helper.WALinuxAgentShim()
        data = shim.register_with_azure_and_fetch_data()
        self.assertEqual(
            [mock.call(self.GoalState.return_value.shared_config_xml)],
            self.iid_from_shared_config_content.call_args_list)
        self.assertEqual(self.iid_from_shared_config_content.return_value,
                         data['instance-id'])

    def test_correct_url_used_for_report_ready(self):
        self.find_endpoint.return_value = 'test_endpoint'
        shim = azure_helper.WALinuxAgentShim()
        shim.register_with_azure_and_fetch_data()
        expected_url = 'http://test_endpoint/machine?comp=health'
        self.assertEqual(
            [mock.call(expected_url, data=mock.ANY, extra_headers=mock.ANY)],
            shim.http_client.post.call_args_list)

    def test_goal_state_values_used_for_report_ready(self):
        self.GoalState.return_value.incarnation = 'TestIncarnation'
        self.GoalState.return_value.container_id = 'TestContainerId'
        self.GoalState.return_value.instance_id = 'TestInstanceId'
        shim = azure_helper.WALinuxAgentShim()
        shim.register_with_azure_and_fetch_data()
        posted_document = shim.http_client.post.call_args[1]['data']
        self.assertIn('TestIncarnation', posted_document)
        self.assertIn('TestContainerId', posted_document)
        self.assertIn('TestInstanceId', posted_document)
