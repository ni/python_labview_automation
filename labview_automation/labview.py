import ConfigParser
from distutils.dir_util import copy_tree
from hoplite.remote_enabler import remotify
import logging
import os
import platform
import psutil
import stat
import subprocess
import tempfile
import time

from labview_automation.client import LabVIEWClient


_log = logging.getLogger(__name__)
_log.addHandler(logging.NullHandler())


class Error(Exception):
    pass


class TimeoutError(Error):
    pass


class LVINIError(Error):
    pass


class NotStartedError(Error):
    pass


class ServerConfiguration(object):
    def __init__(self, start, port, log_path, error_log_path, tcp_timeout_s):
        self._start = start
        self._port = port
        self._log_path = log_path
        self._error_log_path = error_log_path
        self._tcp_timeout_s = tcp_timeout_s

    @property
    def start(self):
        return self._start

    @property
    def port(self):
        return self._port

    @property
    def log_path(self):
        return self._log_path

    @property
    def error_log_path(self):
        return self._error_log_path

    @property
    def tcp_timeout_s(self):
        return self._tcp_timeout_s


class _LVOptions(object):
    """
    Class for convenience in setting LabVIEW Ini tokens.
    """
    def __init__(self):
        # Set some reasonable LabVIEW defaults.
        self.IsFirstLaunch = False
        self.ShowWelcomeOnLaunch = False
        self.prefDlgTestData = 1234
        self.defaultErrorHandlingForNewVIs = False
        self.playAnimatedImages = False
        self.SnapGridDrawAsLines = 1
        self.paletteAsyncLoad = False
        self.paletteLazyLoad = True
        self.autoerr = 3
        self.postScriptLevel2 = False
        self.saveFloaterLocations = True
        self.menuSetup = '"default"'
        self.simpleDiagramHelp = False
        self.GSW_RSSCheckEnabled = False
        self.AutoSaveEnabled = False
        self.nirviShowCompileWarning = False
        self.FPGADialogControl = '"compileSummary;compileWarning;useOldBitfile"'
        self.showDetailsInLoadingDialog = True

    def get_dict(self):
        return {
            'LabVIEW': self.__dict__
        }


class LabVIEW(object):
    """
    Class to control an instance of LabVIEW such as starting or killing it,
    changing ini settings and so forth.
    """
    def __init__(self, host="localhost", version=None, bitness=None,
                 start_with_server=True, server_port=2552,
                 server_log_path=None,
                 server_error_log_path=None,
                 server_tcp_timeout_s=60):
        """
        Args:
            version (string): The version of LabVIEW to control.  If
                unspecified the active version will be used and bitness will
                be ignored.
            bitness (string): Can be either x86 or x64.  Used to specify the
                copy to be controlled.  If both version and bitness are None,
                the active version will be used.  If version is not None then
                this will prefer x86.
        """
        if host == 'localhost':
            self._helpers = LabVIEWHelpers()
        else:
            self._helpers = RemoteLabVIEWHelpers(host)
        self._host = host
        self._path = None
        self._executable = None
        self._version = version
        if bitness is not None and bitness.lower() not in ['x86', 'x64']:
            raise ValueError(
                 "Bitness must be one of 'x86', 'x64', or None.  Specfied"
                 " {}".format(bitness))
        self._bitness = bitness
        self._pid = None
        self.ini = _LVOptions()
        self._server_cfg = ServerConfiguration(
            start_with_server,
            server_port,
            server_log_path,
            server_error_log_path,
            server_tcp_timeout_s
        )

    @property
    def server_cfg(self):
        return self._server_cfg

    @property
    def path(self):
        if self._path is None:
            if self._version is None:
                self._path = self._helpers.get_active_labview_path()
            else:
                os_64bit = self._helpers.is_os_64bit()
                if (self._bitness == 'x64' and not os_64bit):
                    raise ValueError(
                        "Bitness cannot be 'x64' if the OS is 32-bit")
                possible_canidates = self._helpers.get_labview_paths()
                for candidate in possible_canidates:
                    if (os_64bit and self._bitness == 'x86' and
                            'program files (x86)' not in candidate.lower()):
                        continue
                    if (self._bitness == 'x64' and
                            'program files (x86)' in candidate.lower()):
                        continue
                    if self._version.lower() in candidate.lower():
                        self._path = candidate
                        break
        return self._path

    @property
    def executable(self):
        return os.path.join(self.path, "LabVIEW.exe")

    def client(self):
        """
        Returns the LabVIEW Client for this instance.
        """
        if not self.is_running():
            raise NotStartedError("You must start LabVIEW to get talk to it.")
        if not self.server_cfg.start:
            raise NotStartedError("You must start LabVIEW with the automation"
                                  " server to use the LabVIEW Client.")
        return LabVIEWClient(self._host, port=self.server_cfg.port)

    def disable_dialogs(self):
        self.ini.DeployDlgCloseWindow = True
        self.ini.nirviShowErrorDialogs = False
        self.ini.nirviShowErrorDialogsOld = False
        self.ini.NiFpga_BuildPrompt_SelectCompileServer = False
        self.ini.DWarnDialog = False
        self.ini.SuppressRTConnectionDialogs = True
        self.ini.neverShowAddonLicensingStartup = True
        self.ini.neverShowLicensingStartupDialog = True
        self.ini.SaveChanges_ApplyToAll = True
        self.ini.SaveChangesAutoSelection = '"dont"'
        self.ini.autoerr = 3
        self.ini.NIERShowFatalDialog = 0
        self.ini.NIERFatalAutoSend = True
        self.ini.NIERSendDialogClose = True
        self.ini.NIERShowNonFatalDialogOnExit = False
        self.ini.NIERAutoSendAndSuppressAllDialogs = True
        self.ini.AutoSaveEnabled = False

    def disable_ni_error_reporting(self):
        self.ini.NIER = False

    def add_to_search_path(self, path, append=False):
        ini_paths = ''
        try:
            ini_paths = self.ini.viSearchPath
        except AttributeError:
            ini_paths = r"<topvi>:\*;<foundvi>:\;<vilib>:\*;<userlib>:\*;<instrlib>:\*"

        ini_paths = ini_paths.strip('"\'')
        ini_paths = ini_paths.split(';')
        if path not in ini_paths:
            if append:
                ini_paths.append(path)
            else:
                ini_paths.insert(0, path)

        ini_paths = '"{}"'.format(
            (';' if len(ini_paths) > 2 else '').join(ini_paths))

        self.ini.viSearchPath = ini_paths

    def set_number_of_execution_threads(self, number_of_threads):
        # TODO: Implement
        raise NotImplementedError()

    def enable_vi_server(self):
        # TODO: Implement
        raise NotImplementedError()

    def copy_to_labview_dir(self, source, relative_destination):
        labview_path = os.path.join(self.path, relative_destination)
        self._helpers.copy_tree(source, labview_path)
        self._helpers.make_writable(labview_path)
        return labview_path

    def start(self, wait_until_open=True, timeout_s=900):
        ini_file = self._helpers.create_temp_ini(self.ini.get_dict())
        if self.server_cfg.start:
            if self.is_running():
                _log.warning("This LabVIEW instance was already launched.")
                with self.client():
                    pass
                return
            server_vi_path = self._helpers.get_listener_vi_path()
            args = [server_vi_path,
                    '-pref',
                    ini_file,
                    '--',
                    '--port', str(self.server_cfg.port),
                    '--timeout', str(self.server_cfg.tcp_timeout_s*1000)]
            if self.server_cfg.log_path is not None:
                args.append('--reportfile')
                args.append(self.server_cfg.log_path)
            if self.server_cfg.error_log_path is not None:
                args.append('--errorfile')
                args.append(self.server_cfg.error_log_path)
            self.start_with_args(args)
            if wait_until_open:
                self.wait_until_server_loaded(
                    timeout_s, port=self.server_cfg.port)
        else:
            if self.is_running():
                _log.warning("This LabVIEW instance was already launched.")
                return
            self._pid = self._helpers.start_process([
                self.executable,
                '-pref',
                ini_file])

    def restart(self, wait_until_open=True, timeout_s=900):
        if self.is_running():
            self.kill(timeout_s)
        self.start(wait_until_open, timeout_s)

    def start_with_args(self, args):
        run_args = [self.executable]
        run_args = run_args + args

	for proc in psutil.process_iter(['pid', 'name']):
		if(proc.info['name'] == "LabVIEW.exe"):
			self._pid = proc.info['pid']

	if(self._pid == None):
        	self._pid = self._helpers.start_process(run_args)
	else:
		self._helpers.start_process(run_args)

    def is_running(self):
        return self._helpers.process_is_running(self._pid, self.executable)

    def memory_usage(self):
        return self._helpers.get_process_memory_usage(self._pid,
                                                      self.executable)

    def wait_until_server_loaded(self, timeout_s=900, port=2552):
        """
        This function will wait until the LabVIEW server is loaded on the local
        machine.

        :param timeout_s: How long to wait for the LabVIEW server to load.
            Default is 15 minutes (900 seconds).
        :raises TimeoutError: Raised when timeout exceeded while waiting
        """
        waiting = True
        start_time = time.time()
        while waiting:
            try:
                with self.client():
                    pass
            except Exception:
                if time.time() - start_time > timeout_s:
                    raise TimeoutError('Timed out while waiting for LabVIEW'
                                       ' server to load')
            else:
                waiting = False

    def kill(self, timeout_s=None):
        self._helpers.kill_process(self._pid, self.executable, timeout_s)
        self._pid = None


@remotify(__name__)
class LabVIEWHelpers(object):
    def is_os_64bit(self):
        return platform.machine().endswith('64')

    def get_labview_paths(self):
        """
        Get the local absolute paths for all version of LabVIEW development
        environment installed

        :return: Array of strings each indicating the root LV install directory
        """

        opsys = 'windows'
        if opsys == 'windows':
            return self._get_labview_paths_windows()
        else:
            raise NotImplementedError('This command is only supported on'
                                      ' Windows platforms')

    def _get_labview_paths_windows(self):
        # Connect to the registry at the root of the LabVIEW key
        import _winreg
        reg = _winreg.ConnectRegistry(None, _winreg.HKEY_LOCAL_MACHINE)

        installed_lv_paths = []
        # Iterate over all subkeys until you find one that isn't a version
        # number.  Each subkey represents a currently installed version of
        # LabVIEW. We want to pull from that the key indicating its install
        # path. We put each one into an array
        for root_key in [r"SOFTWARE\Wow6432Node\National Instruments\LabVIEW",
                         r"SOFTWARE\National Instruments\LabVIEW"]:
            try:
                key = self._open_windows_native_key(reg, root_key)
                i = 0
                current_lv_version = _winreg.EnumKey(key, i)
                while current_lv_version.find('.') != -1:
                    try:
                        current_lv_reg_key = self._open_windows_native_key(
                            reg, root_key + "\\" + current_lv_version)
                        value, key_type = _winreg.QueryValueEx(
                            current_lv_reg_key, "PATH")
                    except WindowsError as e:
                        if e.errno == 2:
                            pass
                    else:
                        installed_lv_paths.append(value)
                    finally:
                        i += 1
                        try:
                            current_lv_version = _winreg.EnumKey(key, i)
                        except (IOError, WindowsError):
                            break
            except WindowsError as e:
                # If the key doesn't exist LabVIEW may not be installed.  We
                # can safely ignore this error
                pass

        return installed_lv_paths

    def _open_windows_native_key(self, key, sub_key):
        """
        Opens a windows registry key using the OS bitness-based version of the
        registry view.
        This method eventually calls the _winreg.OpenKey() method.

        This is useful because if we're running a 32-bit python interpreter, by
        default _winreg accesses the 32-bit registry view. This is a problem on
        64-bit OSes as it limits us to registries of 32-bit applications.
        """
        import _winreg

        python_bitness, linkage = platform.architecture()

        # If we're running 32-bit python, by default _winreg accesses the
        # 32-bit registry view. This is a problem on 64-bit OSes.
        if python_bitness == '32bit' and platform.machine().endswith('64'):
            # Force _winreg to access the 64-bit registry view with the access
            # map as _winreg.KEY_WOW64_64KEY
            return _winreg.OpenKey(key, sub_key, 0, _winreg.KEY_READ | _winreg.KEY_WOW64_64KEY)
        else:
            return _winreg.OpenKey(key, sub_key)

        return key

    def get_active_labview_path(self):
        """
        Get the local absolute path for the active version of LabVIEW
        # TODO: This needs to be fixed to deal with both 32 and 64 bit
        LabVIEW for the same version installed at the same time.  It favors
        64 bit in this case.

        :return: String indicating the root LV install directory
        """
        opsys = 'windows'
        if opsys == 'windows':
            return self._get_active_labview_windows()
        else:
            raise NotImplementedError('This command is only supported on'
                                      ' Windows platforms')

    def _get_active_labview_windows(self):
        # Connect to the registry at the root of the LabVIEW key
        import _winreg

        reg = _winreg.ConnectRegistry(None, _winreg.HKEY_LOCAL_MACHINE)
        try:
            key = self._open_windows_native_key(
                reg, r"SOFTWARE\National Instruments\LabVIEW\CurrentVersion")
            value, key_type = _winreg.QueryValueEx(key, "PATH")
        except WindowsError:
            key = self._open_windows_native_key(
                reg,
                r"SOFTWARE\Wow6432Node\National Instruments\LabVIEW\CurrentVersion")
            value, key_type = _winreg.QueryValueEx(key, "PATH")
        return value

    def get_listener_vi_path(self):
        return os.path.realpath(
                os.path.join(
                    os.path.dirname(__file__),
                    '..',
                    'lv_listener',
                    'Listener',
                    'Listener Launcher',
                    'Splash Screen.vi'))

    def start_process(self, args):
        """
        Starts a process using Popen and returns its pid.
        """
        proc = subprocess.Popen(args)
        return proc.pid

    def _get_process(self, pid, executable):
        """
        This function attempts to retrieve the psutil.Process for the launched
        version of LabVIEW.

        There is a possibility that the previously launched process
        has closed and a new process with the same pid has been launched,
        this will do a basic check for this by making sure the pid is
        the same LabVIEW exe, there is a very remote chance that LabVIEW
        has relaunched with the same pid, but this seems to be so
        remote and usually irrelevant to our use-cases anyway that I'm
        allowing it here.

        Returns:
            psutil.Process if exists, None otherwise
        """

        if pid is None:
            return None
        try:
            proc = psutil.Process(pid)
            if proc.exe().lower() == executable.lower():
                return proc
            return None
        except psutil.NoSuchProcess:
            return None

    def process_is_running(self, pid, executable):
        proc = self._get_process(pid, executable)
        if proc is None:
            return False
        return proc.is_running()

    def get_process_memory_usage(self, pid, executable):
        proc = self._get_process(pid, executable)
        if proc is None:
            return 0
        return proc.memory_info()[0]

    def kill_process(self, pid, executable, timeout=None):
        proc = self._get_process(pid, executable)
        if proc is None:
            return  # Process not running, just exit.
        proc.kill()
        proc.wait(timeout)

    def copy_tree(self, source, destination):
        copy_tree(source, destination)

    def make_writable(directory):
        os.chmod(directory, stat.S_IWUSR & stat.S_IWOTH)

    def create_temp_ini(self, options={}):
        ini = ConfigParser.RawConfigParser()
        # iterate over sections
        for section, tokens in options.iteritems():
            ini.add_section(section)
            for key, value in tokens.iteritems():
                ini.set(section, key, value)

        fd, path = tempfile.mkstemp(suffix=".ini")
        with os.fdopen(fd, 'w') as f:
            ini.write(f)
        return path


class RemoteLabVIEWHelpers(object):
    def __init__(self, host):
        self.host = host
        self.helpers = LabVIEWHelpers()

    def is_os_64bit(self):
        return self.helpers.remote_is_os_64_bit(self.host)

    def get_labview_paths(self):
        return self.helpers.remote_get_labview_paths(self.host)

    def get_active_labview_path(self):
        return self.helpers.remote_get_active_labview_path(self.host)

    def get_listener_vi_path(self):
        return self.helpers.remote_get_listener_vi_path(self.host)

    def start_process(self, args):
        return self.helpers.remote_start_process(self.host, args)

    def process_is_running(self, pid, executable):
        return self.helpers.remote_process_is_running(
            self.host, pid, executable)

    def get_process_memory_usage(self, pid, executable):
        return self.helpers.remote_get_process_memory_usage(
            self.host, pid, executable)

    def kill_process(self, pid, executable, timeout=None):
        return self.helpers.remote_kill_process(self.host, pid, executable)

    def copy_tree(self, source, destination):
        self.helpers.remote_copy_tree(self.host, source, destination)

    def make_writable(self, directory):
        self.helpers.remote_make_writable(self.host, directory)

    def create_temp_ini(self, tokens=[]):
        return self.helpers.remote_create_temp_ini(self.host, tokens)
