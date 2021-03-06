import time

import serial
import binascii
from collections import namedtuple
import logging

logger = logging.getLogger(__name__)

Stats = namedtuple('Stats', 'type name value')

OPERATOR_MAP = {'TELIA': 24001, 'TRE': 24002}
# op: TMO - 310260

# TODO: Make communication with the module in a separate thread. Using a queue
# for communication of AT commands and implement a state-machine for handling
# AT-commands. Also always keep reading thte serial line for URCs

# TODO: Make a socket interface

class CMEError(Exception):
    """CME ERROR on Module"""


class ATError(Exception):
    """AT Command Error"""


class ConnectionTimeoutError(Exception):
    """Module did not connect within the specified time"""


class SaraN211Module:
    BAUDRATE = 9600
    RTSCTS = False

    AT_ENABLE_NETWORK_REGISTRATION = 'AT+CEREG=1'
    AT_ENABLE_SIGNALING_CONNECTION_URC = 'AT+CSCON=1'
    AT_ENABLE_POWER_SAVING_MODE = 'AT+NPSMR=1'
    AT_ENABLE_ALL_RADIO_FUNCTIONS = 'AT+CFUN=1'
    AT_REBOOT = 'AT+NRB'

    AT_GET_IP = 'AT+CGPADDR'

    AT_SEND_TO = 'AT+NSOST=0'
    AT_CHECK_CONNECTION_STATUS = 'AT+CSCON?'
    AT_RADIO_INFORMATION = 'AT+NUESTATS="RADIO"'

    REBOOT_TIME = 0

    def __init__(self, serial_port: str, echo=False):
        self._serial_port = serial_port
        self._serial = serial.Serial(self._serial_port, baudrate=self.BAUDRATE,
                                     rtscts=self.RTSCTS, timeout=300)
        self.echo = echo
        self.ip = None
        # TODO: Maybe impelemtn property that would issue AT commands?
        self.connected = False
        self.available_messages = list()
        # TODO: make a class containing all states
        self.eps_reg_status = None
        self.radio_signal_power = None
        self.radio_total_power = None
        self.radio_tx_power = None
        self.radio_tx_time = None
        self.radio_rx_time = None
        self.radio_cell_id = None
        self.radio_ecl = None
        self.radio_snr = None
        self.radio_earfcn = None
        self.radio_pci = None
        self.radio_rsrq = None

    def reboot(self):
        """Rebooting the module"""
        logger.info('Rebooting module')
        self._at_action(self.AT_REBOOT)
        logger.info('waiting for module to boot up')
        time.sleep(self.REBOOT_TIME)
        self._serial.flushInput()  # Flush the serial ports to get rid of crap.
        self._serial.flushOutput()
        logger.info('Module rebooted')

    def setup(self):
        """Running all commands to get the module up an working"""

        logger.info('Starting initiation process')
        self.enable_signaling_connection_urc()
        self.enable_network_registration()
        self.enable_psm_mode()
        self.enable_radio_functions()
        logger.info('Finished initiation process')

    def enable_psm_mode(self):
        self._at_action(self.AT_ENABLE_POWER_SAVING_MODE)
        logger.info('Enabled Power Save Mode')

    def enable_signaling_connection_urc(self):
        self._at_action(self.AT_ENABLE_SIGNALING_CONNECTION_URC)
        logger.info('Signaling Connection URC enabled')

    def enable_network_registration(self):
        self._at_action(self.AT_ENABLE_NETWORK_REGISTRATION)
        logger.info('Network registration enabled')

    def enable_radio_functions(self):
        self._at_action(self.AT_ENABLE_ALL_RADIO_FUNCTIONS)
        logger.info('All radio functions enabled')

    def connect(self, operator):

        """Will initiate commands to connect to operators network and wait until
        connected."""
        logger.info('Trying to connect to operator {} network'.format(operator))
        # TODO: Handle connection independent of home network or roaming.

        if operator:

            if isinstance(operator, int):
                # Assumes operator id is sent in
                operator_id = operator

            else:
                operator_id = OPERATOR_MAP.get(operator.upper(), None)
                if operator_id is None:
                    raise ValueError('Operator {} is not supported'.format(operator))

            at_command = 'AT+COPS=1,2,"{}"'.format(operator_id)

        else:
            at_command = 'AT+COPS=0'

        self._at_action(at_command)
        self._await_connection(operator=operator)
        logger.info('Connected to {}'.format(operator))

    def create_socket(self, port: int):
        """Creates a socket that can be used to send and recieve data"""
        logger.info('Creating socket on port {}'.format(port))
        at_c = 'AT+NSOCR="DGRAM",17,{}'.format(port)
        socket_id = self._at_action(at_c)
        logger.info('Socket created with id: {}'.format(socket_id))

    def send_udp_data(self, host: str, port: int, data: str):
        """Send a UDP message"""
        logger.info('Sending UDP message to {}:{}  :  {}'.format(host, port, data))
        _data = binascii.hexlify(data.encode()).upper().decode()
        length = len(data)
        atc = '{},"{}",{},{},"{}"'.format(self.AT_SEND_TO, host, port, length, _data)
        result = self._at_action(atc)
        return result

    def receive_udp_data(self):
        """Recieve a UDP message"""
        # TODO: Do getting of data and parsing in callback on URC.
        logger.info('Waiting for UDP message')
        self._read_line_until_contains('+NSONMI')
        message_info = self.available_messages.pop(0)
        message = self._at_action('AT+NSORF={}'.format(message_info.decode()))
        response = self._parse_udp_response(message[0])
        logger.info('Recieved UDP message: {}'.format(response))
        return response

    def _at_action(self, at_command):
        """
        Small wrapper to issue a AT command. Will wait for the Module to return
        OK.
        """
        logger.debug('Applying AT Command: {}'.format(at_command))
        self._write(at_command)
        irc = self._read_line_until_contains('OK')
        if irc is not None:
            logger.debug('AT Command response = {}'.format(irc))
        return irc

    def _write(self, data):
        """
        Writing data to the module is simple. But it needs to end with \r\n
        to accept the command. The module will answer with an empty line as
        acknowledgement
        """
        data_to_send = data
        if isinstance(data, str):  # if someone sent in a string make it bytes
            data_to_send = data.encode()

        if not data_to_send.endswith(b'\r\n'):
            # someone didnt add the CR an LN so we need to send it
            data_to_send += b'\r\n'

        self._serial.write(data_to_send)

        logger.debug('Sent: {}'.format(data_to_send))
        ack = self._serial.readline()

        if self.echo:
            # when echo is on we will have recieved the message we sent and
            # will get it in the ack response read. But it will not send \n.
            # so we can omitt the data we send + i char for the \r
            # TODO: check that the data we recieved acctually is data + \r
            ack = ack[(len(data) + 1):]

        if ack != b'\r\n':
            raise ValueError('Ack was not received properly, received {}'.format(ack))

    @staticmethod
    def _remove_line_ending(line: bytes):
        """
        To not have to deal with line endings in the data we can used this to
        remove them.
        """
        if line.endswith(b'\r\n'):
            return line[:-2]
        else:
            return line

    def _read_line_until_contains(self, slice):
        """
        Similar to read_until, but will read whole lines so we can use proper
        timeout management. Any URC:s that is read will be handled and we will
        return the IRC:s collected.
        """
        _slice = slice
        if isinstance(slice, str):
            _slice = slice.encode()

        data_list = list()
        irc_list = list()

        while True:
            line = self._remove_line_ending(self._serial.readline())

            if line.startswith(b'+'):
                self._process_urc(line)
            elif line == b'OK':
                pass

            elif line.startswith(b'ERROR'):
                raise ATError('Error on AT Command')

            elif line == b'':
                pass

            else:
                irc_list.append(line)  # the can only be an IRC

            if _slice in line:
                data_list.append(line)
                break
            else:
                data_list.append(line)

        clean_list = [response for response in data_list if not response == b'']

        logger.debug('Received: {}'.format(clean_list))

        return irc_list

    @staticmethod
    def _parse_udp_response(message: bytes):
        _message = message.replace(b'"', b'')
        socket, ip, port, length, _data, remaining_bytes = _message.split(b',')
        data = bytes.fromhex(_data.decode())
        return data

    def _process_urc(self, urc: bytes):
        """
        URC = unsolicited result code
        When waiting on answer from the module it is possible that the module
        sends urcs via +commands. So after the urcs are
        collected we run this method to process them.
        """

        callbackmap = {'CSCON': self._update_connection_status_callback,
                       'CEREG': self._update_eps_reg_status_callback,
                       'CGPADDR': self._update_ip_address_callback,
                       'NSONMI': self._add_available_message_callback,
                       'CME ERROR': self._handle_cme_error, }

        _urc = urc.decode()
        logger.debug('Processing URC: {}'.format(_urc))
        urc_id = _urc[1:_urc.find(':')]
        callback = callbackmap.get(urc_id, None)
        if callback:
            callback(urc)
        else:
            logger.debug('Unhandled urc: {}'.format(urc))

    def _handle_cme_error(self, urc: bytes):

        raise CMEError(urc.decode())

    def _add_available_message_callback(self, urc: bytes):
        _urc, data = urc.split(b':')
        result = data.lstrip()
        logger.debug('Recieved data: {}'.format(result))
        self.available_messages.append(result)

    def _update_radio_statistics(self):
        radio_data = self._at_action(self.AT_RADIO_INFORMATION)
        self._parse_radio_stats(radio_data)

    def _update_connection_status_callback(self, urc):
        """
        In the AT urc +CSCON: 1 the last char is indication if the
        connection is idle or connected

        """
        status = bool(int(urc[-1]))
        self.connected = status
        logger.info('Changed the connection status to {}'.format(status))

    def _update_eps_reg_status_callback(self, urc):
        """
        The command could return more than just the status.
        Maybe a regex would be good
        But for now we just check the last as int
        """
        status = int(chr(urc[-1]))
        self.eps_reg_status = status
        logger.info('Updated status EPS Registration = {}'.format(status))

    def _update_ip_address_callback(self, urc: bytes):
        """
        Update the IP Address of the module
        """
        # TODO: this is per socket. Need to implement socket handling
        _urc = urc.decode()
        ip_addr = _urc[(_urc.find('"') + 1):-1]
        self.ip = ip_addr
        logger.info('Updated the IP Address of the module to {}'.format(ip_addr))

    def _parse_radio_stats(self, irc_buffer):

        stats = [self._parse_radio_stats_string(item) for item in irc_buffer]

        for stat in stats:
            if not stat:
                continue
            if stat.type == 'RADIO' and stat.name == 'Signal power':
                self.radio_signal_power = stat.value
            elif stat.type == 'RADIO' and stat.name == 'Total power':
                self.radio_total_power = stat.value
            elif stat.type == 'RADIO' and stat.name == 'TX power':
                self.radio_tx_power = stat.value
            elif stat.type == 'RADIO' and stat.name == 'TX time':
                self.radio_tx_time = stat.value
            elif stat.type == 'RADIO' and stat.name == 'RX time':
                self.radio_rx_time = stat.value
            elif stat.type == 'RADIO' and stat.name == 'Cell ID':
                self.radio_cell_id = stat.value
            elif stat.type == 'RADIO' and stat.name == 'ECL':
                self.radio_ecl = stat.value
            elif stat.type == 'RADIO' and stat.name == 'SNR':
                self.radio_snr = stat.value
            elif stat.type == 'RADIO' and stat.name == 'EARFCN':
                self.radio_earfcn = stat.value
            elif stat.type == 'RADIO' and stat.name == 'PCI':
                self.radio_pci = stat.value
            elif stat.type == 'RADIO' and stat.name == 'RSRQ':
                self.radio_rsrq = stat.value
            else:
                logger.debug('Unhandled statistics data: {}'.format(stat))

    @staticmethod
    def _parse_radio_stats_string(stats_byte_string: bytes):
        """
        The string is like: b'NUESTATS: "RADIO","Signal power",-682'
        :param stats_byte_string:
        :return: NamedTuple Stats
        """
        parts = stats_byte_string.decode().split(':')

        irc = parts[0].strip()
        data = parts[1].strip().replace('"', '')

        data_parts = data.split(',')
        if irc == 'NUESTATS':
            return Stats(data_parts[0], data_parts[1], int(data_parts[2]))
        else:
            return None

    def __repr__(self):
        return 'NBIoTModule(serial_port="{}")'.format(self._serial_port)

    def _await_connection(self, operator):

        logging.info('Awaiting Connection to {}'.format(operator))

        if operator.upper() == 'TELIA':
            self._read_line_until_contains('CEREG: 5')
        elif operator.upper() == 'TRE':
            self._read_line_until_contains('CEREG: 1')
        else:
            raise ValueError('Operator {} is not supported'.format(operator))


class SaraR4Module(SaraN211Module):
    BAUDRATE = 115200
    RTSCTS = 1

    DEFAULT_BANDS = [20]

    AT_CREATE_UDP_SOCKET = 'AT+USOCR=17'
    AT_CREATE_TCP_SOCKET = 'AT+USOCR=6'
    AT_ENABLE_LTE_M_RADIO = 'AT+URAT=7'
    AT_ENABLE_NBIOT_RADIO = 'AT+URAT=8'

    AT_REBOOT = 'AT+CFUN=15'  # R4 specific

    REBOOT_TIME = 10

    def __init__(self, serial_port: str, echo=True):

        super().__init__(serial_port, echo)
        self.sockets = dict()

    def setup(self, radio_mode='NBIOT'):
        self.set_radio_mode(mode=radio_mode)
        self.enable_radio_functions()
        self.enable_network_registration()
        self.set_error_format()
        self.set_data_format()

    def set_data_format(self):

        self._at_action('AT+UDCONF=1,1')  # Set data format to HEX
        logger.info('Data format set to HEX')

    def set_error_format(self):
        self._at_action('AT+CMEE=2')  # enable verbose errors
        logger.info('Verbose errors enabled')

    def set_band_mask(self, bands: list = None):
        """
        Band is set using a bit for each band. Band 1=bit 0, Band 64=Bit 63

        .. note:
            Only supports NB IoT RAT.
        """
        logger.info('Setting Band Mask for bands {}'.format(bands))
        bands_to_set = self.DEFAULT_BANDS
        if bands:
            bands_to_set = bands

        total_band_mask = 0

        for band in bands_to_set:
            individual_band_mask = 1 << (band - 1)
            total_band_mask = total_band_mask | individual_band_mask

        self._at_action('AT+UBANDMASK=1,{},{}'.format(total_band_mask, total_band_mask))

    def set_radio_mode(self, mode):
        # TODO: Move to parent object. And have list of supported radios on object.
        mode_dict = {'NBIOT': self.AT_ENABLE_NBIOT_RADIO,
                     'LTEM': self.AT_ENABLE_LTE_M_RADIO}

        response = self._at_action(mode_dict[mode.upper()])
        logger.info('Radio Mode set to {}'.format(mode))
        return response

    def set_pdp_context(self, apn, pdp_type="IP", cid=1):
        logger.info('Setting PDP Context')
        _at_command = 'AT+CGDCONT={},"{}","{}"'.format(cid, pdp_type, apn)
        self._at_action(_at_command)
        logger.info('PDP Context: {}, {}'.format(apn, pdp_type))

    def create_socket(self, socket_type='UDP', port: int = None):
        # TODO: Move to parent object. Have list of supported socket types.
        logger.info('Creating {} socket'.format(socket_type))
        if socket_type.upper() == 'UDP':
            at_command = self.AT_CREATE_UDP_SOCKET

        elif socket_type.upper() == 'TCP':
            at_command = self.AT_CREATE_TCP_SOCKET

        else:
            raise ValueError(
                'socket_type can only be of type udp|UPD or tcp|TCP')

        if port:
            at_command += ',{}'.format(port)

        result = self._at_action(at_command)

        logger.info('{} socket {} created'.format(socket_type, result))

        return result

    def send_udp_data(self, host: str, port: int, data: str):
        """Send a UDP message"""
        logger.info('Sending UDP message to {}:{}  :  {}'.format(host, port, data))
        _data = binascii.hexlify(data.encode()).upper().decode()
        length = len(data)
        atc = 'AT+USOST=0,"{}",{},{},"{}"'.format(host, port, length, _data) 
        result = self._at_action(atc)
        return result

    def _await_connection(self, operator, timeout=180):
        logging.info('Awaiting Connection to {}'.format(operator))
        start_time = time.time()
        while True:
            time.sleep(2)
            self._at_action('AT+CEREG?')

            if self.eps_reg_status == 0:
                continue

            if operator.upper() == 'TELIA':
                if self.eps_reg_status == 5:
                    break

            elif operator.upper() == 'TRE':
                if self.eps_reg_status == 1:
                    break

            else:
                raise ValueError('Operator {} is not supported'.format(operator))

            elapsed_time = time.time() - start_time
            if elapsed_time > timeout:
                raise ConnectionTimeoutError('Could not connect to {}'.format(operator))
