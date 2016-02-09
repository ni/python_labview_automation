import socket
import struct
import bson


class Error(Exception):
    """
    Class for errors which occur while calling a LabVIEW VI
    """

    def __init__(self, code, source, message):
        self.code = code
        self.source = source
        self.message = message


class LabVIEWClient(object):

    """
    This class is a simple wrapper around TCP methods that communicate with the
    LabVIEW Listener component
    """

    def __init__(self, address, port=2552):
        """
        :param address: Address of the remote computer running the LVListener
                        eg. 10.2.13.32
        :param port: Port of the remote computer LVListener is listening on
                     (default: 2552)
        """
        self.address = address
        self.port = port

    def __enter__(self):
        # Attempt to establish a TCP connection to the listener
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((self.address, self.port))
        self.connection = s
        return self

    def __exit__(self, type, value, tb):
        # Only close the socket if this method is called. This enables the user
        # to call run_vi_synchronous multiple times on this object
        if self.connection:
            self.connection.close()

    def _check_for_error(self, return_dict):
        """
        Checks for an error being returned in the recieved dictionary.
        Raises an error if the status is true.
        """
        if 'RunVIState_Status' not in return_dict:
            return
        if return_dict['RunVIState_Status']:
            error = {'code': return_dict['RunVIState_Code'],
                     'status': return_dict['RunVIState_Status'],
                     'source': return_dict['RunVIState_Source']}
            raise Error(
                return_dict['RunVIState_Code'],
                return_dict['RunVIState_Source'],
                self.describe_error(error))

    def run_vi_synchronous(self, vi_path, control_values, run_options=0,
                           open_frontpanel=False, indicator_names=[]):
        """
        :param vi_path: Absolute path of the VI to run on the remote computer
        :param control_values: Dictionary of the control values to set on the
                               target VI
        :param run_options: Int to specify the LabVIEW run options for the
                            target VI
        :param open_frontpanel: Boolean that specifies whether to open front
                                panel of target VI
        :param indicator_names: Optional list of strings that specifies the
                                names of indicators to return. Returns all
                                indicators if an empty list is specified.
        """
        msg = {'command': 'run_vi',
               'vi_path': vi_path,
               'run_options': run_options,
               'open_frontpanel': open_frontpanel,
               'control_values': control_values,
               'indicator_names': indicator_names}

        self._send_dict(msg)
        return_vals = self._recv_dict()
        self._check_for_error(return_vals)
        return return_vals

    def describe_error(self, error):
        """
        Sends a message to LabVIEW to describe an error.

        :param error: Dictionary containing 'source', 'status', and 'code'
        """
        msg = {'command': 'describe_error',
               'error': error}
        self._send_dict(msg)
        return_vals = self._recv_dict()
        return return_vals['msg']

    def set_controls(self, project_path, target_name, vi_path, control_values,
                     ignore_nonexistent_controls=False):
        """
        Sets controls on a VI under a specified target of a specified project
        without running the VI. Leaves front panel of the VI open.

        :param project_path: Absolute path of the project which contains the VI
                             to set controls on
        :param target_name: Name of the target in the project which contains
                            the VI to set controls on
        :param vi_path: Absolute path of the target VI to set controls on
        :param control_values: Dictionary of the control values to set on the
                               target VI
        :param ignore_nonexistent_controls: Boolean that specifies whether to
                                            ignore errors if control_values
                                            contain controls that don't exist
                                            in the target VI
        """
        msg = {'command': 'set_controls',
               'project_path': project_path,
               'target_name': target_name,
               'vi_path': vi_path,
               'control_values': control_values,
               'ignore_nonexistent_controls': ignore_nonexistent_controls}

        self._send_dict(msg)
        return_vals = self._recv_dict()
        self._check_for_error(return_vals)
        return return_vals

    def get_indicators(self, project_path, target_name,
                       vi_path, indicator_names):
        """
        Get the specified indicators of a VI under a specified target of a
        specified project without running the VI.

        :param project_path: Absolute path of the project which contains the VI
                             to get the indicators value
        :param target_name: Name of the target in the project which contains
                            the VI to get the indicators values
        :param vi_path: Absolute path of the target VI to get indicators values
        :param indicator_names: List of indicator names of which to get values
        """
        msg = {'command': 'get_indicators',
               'project_path': project_path,
               'target_name': target_name,
               'vi_path': vi_path,
               'indicator_names': indicator_names}

        self._send_dict(msg)
        return_vals = self._recv_dict()
        self._check_for_error(return_vals)
        return return_vals

    def _recv_dict(self):
        if self.connection:
            packet_size = self.connection.recv(4)
            packet_size_int = struct.unpack('l', packet_size)[0]
            # First part of packet will be the 4-byte packet size
            packet = packet_size
            while len(packet) < packet_size_int:
                partial_packet = self.connection.recv(
                    packet_size_int - len(packet))
                if not partial_packet:
                    break
                packet += partial_packet
            return bson.decode_all(packet)[0]

    def _send_dict(self, msg):
        if self.connection:
            bson_msg = bson.BSON.encode(msg)
            self.connection.send(bson_msg)
