#!/usr/bin/env python3
# this module is part of undetected_chromedriver

from distutils.version import LooseVersion
import io
import json
import logging
import os
import pathlib
import platform
import random
import re
import shutil
import string
import sys
import time
from urllib.request import urlopen
from urllib.request import urlretrieve
import zipfile
from multiprocessing import Lock
import subprocess

logger = logging.getLogger(__name__)

IS_POSIX = sys.platform.startswith(("darwin", "cygwin", "linux", "linux2"))

class Chrome_Version():
    def get_chrome_major_version():
        """
        Detects the major version number of the installed chrome/chromium browser.
        :return: The browsers major version number or None
        """
        browser_executables = ['google-chrome', 'chrome', 'chrome-browser', 'google-chrome-stable', 'chromium',
                               'chromium-browser']
        for browser_executable in browser_executables:
            try:
                version = subprocess.check_output([browser_executable, '--version'])
                return int(re.match(r'.*?((?P<major>\d+)\.(\d+\.){2,3}\d+).*?', version.decode('utf-8')).group('major'))
            except Exception:
                pass

    def get_chromedriver_version(version):
        """Return needed Chromedriver Version"""
        chrome2chromdriver = {126:"31.4.0", 122: "29.1.5", 114: "25.0.0-beta.2", 112: "24.2.0",
                              111: "24.0.0-beta.2", 110: "23.3.1", 108: "22.3.8", 106: "21.4.4", 104: "20.3.12",
                              102: "19.1.9", 100: "18.3.15", 98: "17.4.11", 96: "16.2.8",
                              95: "16.0.0-alpha.1", 94: "15.5.7", 93: "14.2.9", 92: "14.0.0-beta.1", 91: "13.6.9",
                              89: "12.2.3", 87: "11.5.0", 85: "10.4.7", 83: "9.4.4", 82: "9.0.0-beta.14"}
        nearest_version = min(chrome2chromdriver, key=lambda x: abs(x - version))

        return chrome2chromdriver[nearest_version]


    
class Patcher(object):
    arch = os.uname().machine
    if arch == "aarch64":
        arch = "arm64"
    if arch != "x86_64":
        chromedriver_version = Chrome_Version.get_chromedriver_version(Chrome_Version.get_chrome_major_version())
    
    lock = Lock()
    exe_name = "chromedriver%s"

    platform = sys.platform
    if platform.endswith("win32"):
        d = "~/appdata/roaming/undetected_chromedriver"
    elif "LAMBDA_TASK_ROOT" in os.environ:
        d = "/tmp/undetected_chromedriver"
    elif platform.startswith(("linux", "linux2")):
        d = "~/.local/share/undetected_chromedriver"
    elif platform.endswith("darwin"):
        d = "~/Library/Application Support/undetected_chromedriver"
    else:
        d = "~/.undetected_chromedriver"
    data_path = os.path.abspath(os.path.expanduser(d))

    def __init__(
        self,
        executable_path=None,
        force=False,
        version_main: int = 0,
        user_multi_procs=False,
    ):
        """
        Args:
            executable_path: None = automatic
                             a full file path to the chromedriver executable
            force: False
                    terminate processes which are holding lock
            version_main: 0 = auto
                specify main chrome version (rounded, ex: 82)
        """
        arch = os.uname().machine
        if arch == "aarch64":
            arch = "arm64"
        if arch != "x86_64":
            chromedriver_version = Chrome_Version.get_chromedriver_version(Chrome_Version.get_chrome_major_version())
    
        self.force = force
        self._custom_exe_path = False
        prefix = "undetected"
        self.user_multi_procs = user_multi_procs

        self.is_old_chromedriver = version_main and version_main <= 114
        # Needs to be called before self.exe_name is accessed
        self._set_platform_name()

        if not os.path.exists(self.data_path):
            os.makedirs(self.data_path, exist_ok=True)

        if not executable_path:
            self.executable_path = os.path.join(
                self.data_path, "_".join([prefix, self.exe_name])
            )

        if not IS_POSIX:
            if executable_path:
                if not executable_path[-4:] == ".exe":
                    executable_path += ".exe"

        self.zip_path = os.path.join(self.data_path, prefix)

        if not executable_path:
            if not self.user_multi_procs:
                self.executable_path = os.path.abspath(
                    os.path.join(".", self.executable_path)
                )

        if executable_path:
            self._custom_exe_path = True
            self.executable_path = executable_path

        # Set the correct repository to download the Chromedriver from
        if arch == "x86_64":
            if self.is_old_chromedriver:
                self.url_repo = "https://chromedriver.storage.googleapis.com"
            else:
                self.url_repo = "https://googlechromelabs.github.io/chrome-for-testing"
        else:
            self.url_repo = "https://github.com/electron/electron/releases/download/v%s/"
            url_repo %= chromedriver_version
        
        self.version_main = version_main
        self.version_full = None

    def _set_platform_name(self):
        """
        Set the platform and exe name based on the platform undetected_chromedriver is running on
        in order to download the correct chromedriver.
        """
        if self.platform.endswith("win32"):
            self.platform_name = "win32"
            self.exe_name %= ".exe"
        if self.platform.endswith(("linux", "linux2")):
            self.platform_name = "linux64"
            self.exe_name %= ""
        if self.platform.endswith("darwin"):
            if self.is_old_chromedriver:
                self.platform_name = "mac64"
            else:
                self.platform_name = "mac-x64"
            self.exe_name %= ""

    def auto(self, executable_path=None, force=False, version_main=None, _=None):
        """

        Args:
            executable_path:
            force:
            version_main:

        Returns:

        """
        p = pathlib.Path(self.data_path)
        if self.user_multi_procs:
            with Lock():
                files = list(p.rglob("*chromedriver*"))
                most_recent = max(files, key=lambda f: f.stat().st_mtime)
                files.remove(most_recent)
                list(map(lambda f: f.unlink(), files))
                if self.is_binary_patched(most_recent):
                    self.executable_path = str(most_recent)
                    return True

        if executable_path:
            self.executable_path = executable_path
            self._custom_exe_path = True

        if self._custom_exe_path:
            ispatched = self.is_binary_patched(self.executable_path)
            if not ispatched:
                return self.patch_exe()
            else:
                return

        if version_main:
            self.version_main = version_main
        if force is True:
            self.force = force

        try:
            os.unlink(self.executable_path)
        except PermissionError:
            if self.force:
                self.force_kill_instances(self.executable_path)
                return self.auto(force=not self.force)
            try:
                if self.is_binary_patched():
                    # assumes already running AND patched
                    return True
            except PermissionError:
                pass
            # return False
        except FileNotFoundError:
            pass

        if "arm" in self.arch:
            pass
        else:
            release = self.fetch_release_number()
            self.version_main = release.version[0]
            self.version_full = release
        self.unzip_package(self.fetch_package())
        return self.patch()

    def driver_binary_in_use(self, path: str = None) -> bool:
        """
        naive test to check if a found chromedriver binary is
        currently in use

        Args:
            path: a string or PathLike object to the binary to check.
                  if not specified, we check use this object's executable_path
        """
        if not path:
            path = self.executable_path
        p = pathlib.Path(path)

        if not p.exists():
            raise OSError("file does not exist: %s" % p)
        try:
            with open(p, mode="a+b") as fs:
                exc = []
                try:

                    fs.seek(0, 0)
                except PermissionError as e:
                    exc.append(e)  # since some systems apprently allow seeking
                    # we conduct another test
                try:
                    fs.readline()
                except PermissionError as e:
                    exc.append(e)

                if exc:

                    return True
                return False
            # ok safe to assume this is in use
        except Exception as e:
            # logger.exception("whoops ", e)
            pass

    def cleanup_unused_files(self):
        p = pathlib.Path(self.data_path)
        items = list(p.glob("*undetected*"))
        for item in items:
            try:
                item.unlink()
            except:
                pass

    def patch(self):
        self.patch_exe()
        return self.is_binary_patched()

    def fetch_release_number(self):
        """
        Gets the latest major version available, or the latest major version of self.target_version if set explicitly.
        :return: version string
        :rtype: LooseVersion
        """
        # Endpoint for old versions of Chromedriver (114 and below)
        if self.is_old_chromedriver:
            path = f"/latest_release_{self.version_main}"
            path = path.upper()
            logger.debug("getting release number from %s" % path)
            return LooseVersion(urlopen(self.url_repo + path).read().decode())

        # Endpoint for new versions of Chromedriver (115+)
        if not self.version_main:
            # Fetch the latest version
            path = "/last-known-good-versions-with-downloads.json"
            logger.debug("getting release number from %s" % path)
            with urlopen(self.url_repo + path) as conn:
                response = conn.read().decode()

            last_versions = json.loads(response)
            return LooseVersion(last_versions["channels"]["Stable"]["version"])

        # Fetch the latest minor version of the major version provided
        path = "/latest-versions-per-milestone-with-downloads.json"
        logger.debug("getting release number from %s" % path)
        with urlopen(self.url_repo + path) as conn:
            response = conn.read().decode()

        major_versions = json.loads(response)
        return LooseVersion(major_versions["milestones"][str(self.version_main)]["version"])

    def parse_exe_version(self):
        with io.open(self.executable_path, "rb") as f:
            for line in iter(lambda: f.readline(), b""):
                match = re.search(rb"platform_handle\x00content\x00([0-9.]*)", line)
                if match:
                    return LooseVersion(match[1].decode())

    def fetch_package(self):
        """
        Downloads ChromeDriver from source

        :return: path to downloaded file
        """
        arch = os.uname().machine
        if arch == "aarch64":
            arch = "arm64"
        if arch != "x86_64":
            chromedriver_version = Chrome_Version.get_chromedriver_version(Chrome_Version.get_chrome_major_version())
    
        if arch == "x86_64":
            zip_name = f"chromedriver_{self.platform_name}.zip"
        else:
            zip_name = f"chromedriver-v{chromedriver_version}-linux-{arch}.zip"

        if "arm" in self.arch:
            download_url = self.url_repo + self.zip_name
        else:
            if self.is_old_chromedriver:
                download_url = "%s/%s/%s" % (self.url_repo, self.version_full.vstring, zip_name)
            else:
                zip_name = zip_name.replace("_", "-", 1)
                download_url = "https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/%s/%s/%s"
                download_url %= (self.version_full.vstring, self.platform_name, zip_name)

        logger.debug("downloading from %s" % download_url)
        print(download_url)
        return urlretrieve(download_url)[0]

    def unzip_package(self, fp):
        """
        Does what it says

        :return: path to unpacked executable
        """
        exe_path = self.exe_name
        if not self.is_old_chromedriver:
            # The new chromedriver unzips into its own folder
            zip_name = f"chromedriver-{self.platform_name}"
            exe_path = os.path.join(zip_name, self.exe_name)

        logger.debug("unzipping %s" % fp)
        try:
            os.unlink(self.zip_path)
        except (FileNotFoundError, OSError):
            pass

        os.makedirs(self.zip_path, mode=0o755, exist_ok=True)
        with zipfile.ZipFile(fp, mode="r") as zf:
            zf.extractall(self.zip_path)
        os.rename(os.path.join(self.zip_path, exe_path), self.executable_path)
        os.remove(fp)
        shutil.rmtree(self.zip_path)
        os.chmod(self.executable_path, 0o755)
        print(self.executable_path)
        return self.executable_path

    @staticmethod
    def force_kill_instances(exe_name):
        """
        kills running instances.
        :param: executable name to kill, may be a path as well

        :return: True on success else False
        """
        exe_name = os.path.basename(exe_name)
        if IS_POSIX:
            r = os.system("kill -f -9 $(pidof %s)" % exe_name)
        else:
            r = os.system("taskkill /f /im %s" % exe_name)
        return not r

    @staticmethod
    def gen_random_cdc():
        cdc = random.choices(string.ascii_letters, k=27)
        return "".join(cdc).encode()

    def is_binary_patched(self, executable_path=None):
        executable_path = executable_path or self.executable_path
        try:
            with io.open(executable_path, "rb") as fh:
                return fh.read().find(b"undetected chromedriver") != -1
        except FileNotFoundError:
            return False

    def patch_exe(self):
        start = time.perf_counter()
        logger.info("patching driver executable %s" % self.executable_path)
        with io.open(self.executable_path, "r+b") as fh:
            content = fh.read()
            # match_injected_codeblock = re.search(rb"{window.*;}", content)
            match_injected_codeblock = re.search(rb"\{window\.cdc.*?;\}", content)
            if match_injected_codeblock:
                target_bytes = match_injected_codeblock[0]
                new_target_bytes = (
                    b'{console.log("undetected chromedriver 1337!")}'.ljust(
                        len(target_bytes), b" "
                    )
                )
                new_content = content.replace(target_bytes, new_target_bytes)
                if new_content == content:
                    logger.warning(
                        "something went wrong patching the driver binary. could not find injection code block"
                    )
                else:
                    logger.debug(
                        "found block:\n%s\nreplacing with:\n%s"
                        % (target_bytes, new_target_bytes)
                    )
                fh.seek(0)
                fh.write(new_content)
        logger.debug(
            "patching took us {:.2f} seconds".format(time.perf_counter() - start)
        )

    def __repr__(self):
        return "{0:s}({1:s})".format(
            self.__class__.__name__,
            self.executable_path,
        )

    def __del__(self):
        if self._custom_exe_path:
            # if the driver binary is specified by user
            # we assume it is important enough to not delete it
            return
        else:
            timeout = 3  # stop trying after this many seconds
            t = time.monotonic()
            now = lambda: time.monotonic()
            while now() - t > timeout:
                # we don't want to wait until the end of time
                try:
                    if self.user_multi_procs:
                        break
                    os.unlink(self.executable_path)
                    logger.debug("successfully unlinked %s" % self.executable_path)
                    break
                except (OSError, RuntimeError, PermissionError):
                    time.sleep(0.01)
                    continue
                except FileNotFoundError:
                    break
