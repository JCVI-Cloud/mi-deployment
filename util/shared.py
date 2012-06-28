""" Shared functionality useful for multiple package managers.
    Code adapted from Brad Chapman and https://github.com/chapmanb/cloudbiolinux
"""
import os
import yaml
from contextlib import contextmanager

from fabric.api import *
from fabric.contrib.files import *
from fabric.colors import yellow

def _yaml_to_packages(yaml_file, to_install, subs_yaml_file=None):
    """ Read a list of packages from a YAML configuration file and return
        it as a list.
    """
    print(yellow("Reading %s" % yaml_file))
    with open(yaml_file) as in_handle:
        full_data = yaml.load(in_handle)
    if subs_yaml_file is not None:
        with open(subs_yaml_file) as in_handle:
            subs = yaml.load(in_handle)
    else:
        subs = {}
    # Filter the data based on what we have configured to install
    data = [(k, v) for (k, v) in full_data.iteritems()
            if to_install is None or k in to_install]
    data.sort()
    packages = [] # List of packages to install
    pkg_to_group = dict() # Back pointer - keep track bc. of which app is a package being added
    while len(data) > 0:
        cur_key, cur_info = data.pop(0)
        if cur_info:
            if isinstance(cur_info, (list, tuple)):
                packages.extend(_filter_subs_packages(cur_info, subs))
                for p in cur_info:
                    pkg_to_group[p] = cur_key
            elif isinstance(cur_info, dict):
                for key, val in cur_info.iteritems():
                    data.append((cur_key, val))
            else:
                raise ValueError(cur_info)
    print(yellow("Packages to install: {0}".format(", ".join(packages))))
    return packages, pkg_to_group

def _filter_subs_packages(initial, subs):
    """ Rename and filter package list with subsitutions; for similar systems.
    """
    final = []
    for p in initial:
        try:
            new_p = subs[p]
        except KeyError:
            new_p = p
        if new_p:
            final.append(new_p)
    return sorted(final)

# -- decorators and context managers

def _if_not_installed(pname):
    """Decorator that checks if a callable program is installed.
    """
    def argcatcher(func):
        def decorator(*args, **kwargs):
            with settings(
                    hide('warnings', 'running', 'stdout', 'stderr'),
                    warn_only=True):
                result = run(pname)
            if result.return_code == 127:
                return func(*args, **kwargs)
        return decorator
    return argcatcher

def _if_not_python_lib(library):
    """Decorator that checks if a python library is installed.
    """
    def argcatcher(func):
        def decorator(*args, **kwargs):
            with settings(warn_only=True):
                pyver = env.python_version_ext if env.has_key("python_version_ext") else ""
                result = run("python%s -c 'import %s'" % (pyver, library))
            if result.failed:
                return func(*args, **kwargs)
        return decorator
    return argcatcher

@contextmanager
def _make_tmp_dir():
    tmp_dir = run("echo $TMPDIR").strip()
    if not tmp_dir:
        home_dir = run("echo $HOME")
        tmp_dir = os.path.join(home_dir, "tmp")
    work_dir = os.path.join(tmp_dir, "mi_deployment_tmp")
    if not exists(work_dir):
        run("mkdir -p %s" % work_dir)
    yield work_dir
    if exists(work_dir):
        run("rm -rf %s" % work_dir)

# -- Standard build utility simplifiers

def _get_expected_file(url):
    tar_file = os.path.split(url)[-1]
    safe_tar = "--pax-option='delete=SCHILY.*,delete=LIBARCHIVE.*'"
    exts = {(".tar.gz", ".tgz") : "tar %s -xzpf" % safe_tar,
            (".tar.bz2",): "tar %s -xjpf" % safe_tar,
            (".zip",) : "unzip"}
    for ext_choices, tar_cmd in exts.iteritems():
        for ext in ext_choices:
            if tar_file.endswith(ext):
                return tar_file, tar_file[:-len(ext)], tar_cmd
    raise ValueError("Did not find extract command for %s" % url)

def _safe_dir_name(dir_name, need_dir=True):
    replace_try = ["", "-src", "_core"]
    for replace in replace_try:
        check = dir_name.replace(replace, "")
        if exists(check):
            return check
    # still couldn't find it, it's a nasty one
    for check_part in (dir_name.split("-")[0].split("_")[0],
                       dir_name.split("-")[-1].split("_")[-1],
                       dir_name.split(".")[0]):
        with settings(hide('warnings', 'running', 'stdout', 'stderr'),
                      warn_only=True):
            dirs = run("ls -d1 *%s*/" % check_part).split("\n")
            dirs = [x for x in dirs if "cannot access" not in x and "No such" not in x]
        if len(dirs) == 1:
            return dirs[0]
    if need_dir:
        raise ValueError("Could not find directory %s" % dir_name)

def _fetch_and_unpack(url, need_dir=True):
    if url.startswith(("git", "svn", "hg", "cvs")):
        run(url)
        base = os.path.basename(url.split()[-1])
        return os.path.splitext(base)[0]
    else:
        tar_file, dir_name, tar_cmd = _get_expected_file(url)
        if not exists(tar_file):
            run("wget --no-check-certificate %s" % url)
        run("%s %s" % (tar_cmd, tar_file))
        return _safe_dir_name(dir_name, need_dir)

def _configure_make(env, install_path=None):
    run("./configure --disable-werror --prefix=%s " % install_path if install_path else env.system_install)
    run("make")
    env.safe_sudo("make install")

def _make_copy(find_cmd=None, premake_cmd=None, do_make=True):
    def _do_work(env):
        if premake_cmd:
            premake_cmd()
        if do_make:
            run("make")
        if find_cmd:
            install_dir = os.path.join(env.system_install, "bin")
            for fname in run(find_cmd).split("\n"):
                env.safe_sudo("mv -f %s %s" % (fname.rstrip("\r"), install_dir))
    return _do_work

def _get_install(url, env, make_command, post_unpack_fn=None, install_path=None):
    """Retrieve source from a URL and install in our system directory.
    """
    with _make_tmp_dir() as work_dir:
        with cd(work_dir):
            dir_name = _fetch_and_unpack(url)
            with cd(dir_name):
                if post_unpack_fn:
                    post_unpack_fn(env)
                make_command(env, install_path)

def _get_install_local(url, env, make_command, dir_name=None):
    """Build and install in a local directory.
    """
    (_, test_name, _) = _get_expected_file(url)
    test1 = os.path.join(env.local_install, test_name)
    if dir_name is not None:
        test2 = os.path.join(env.local_install, dir_name)
    elif "-" in test1:
        test2, _ = test1.rsplit("-", 1)
    else:
        test2 = os.path.join(env.local_install, test_name.split("_")[0])
    if not exists(test1) and not exists(test2):
        with _make_tmp_dir() as work_dir:
            with cd(work_dir):
                dir_name = _fetch_and_unpack(url)
                if not exists(os.path.join(env.local_install, dir_name)):
                    with cd(dir_name):
                        make_command(env)
                    run("mv %s %s" % (dir_name, env.local_install))

# --- Language specific utilities

def _symlinked_java_version_dir(pname, version, env):
    base_dir = os.path.join(env.system_install, "share", "java", pname)
    install_dir = "%s-%s" % (base_dir, version)
    if not exists(install_dir):
        env.safe_sudo("mkdir -p %s" % install_dir)
        if exists(base_dir):
            env.safe_sudo("rm -f %s" % base_dir)
        env.safe_sudo("ln -s %s %s" % (install_dir, base_dir))
        return install_dir
    return None

def _java_install(pname, version, url, env, install_fn=None):
    install_dir = _symlinked_java_version_dir(pname, version, env)
    if install_dir:
        with _make_tmp_dir() as work_dir:
            with cd(work_dir):
                dir_name = _fetch_and_unpack(url)
                with cd(dir_name):
                    if install_fn is not None:
                        install_fn(env, install_dir)
                    else:
                        env.safe_sudo("mv *.jar %s" % install_dir)

def _python_make(env):
    run("python%s setup.py build" % env.python_version_ext)
    env.safe_sudo("python%s setup.py install --skip-build" % env.python_version_ext)
    for clean in ["dist", "build", "lib/*.egg-info"]:
        env.safe_sudo("rm -rf %s" % clean)

def _setup_apt_automation():
    """Setup the environment to be fully automated for tricky installs.

    Sun Java license acceptance:
    http://www.davidpashley.com/blog/debian/java-license

    MySQL root password questions; install with empty root password:
    http://snowulf.com/archives/540-Truly-non-interactive-unattended-apt-get-install.html

    Postfix, setup for no configuration. See more on issues here:
    http://www.uluga.ubuntuforums.org/showthread.php?p=9120196
    """
    interactive_cmd = "export DEBIAN_FRONTEND=noninteractive"
    if not contains(env.shell_config, interactive_cmd):
        append(env.shell_config, interactive_cmd)
    package_info = [
            "postfix postfix/main_mailer_type select No configuration",
            "postfix postfix/mailname string notusedexample.org",
            "mysql-server-5.1 mysql-server/root_password string '(password omitted)'",
            "mysql-server-5.1 mysql-server/root_password_again string '(password omitted)'",
            "sun-java6-jdk shared/accepted-sun-dlj-v1-1 select true",
            "sun-java6-jre shared/accepted-sun-dlj-v1-1 select true",
            "sun-java6-bin shared/accepted-sun-dlj-v1-1 select true",
            "grub-pc grub2/linux_cmdline string ''",
            "grub-pc grub-pc/install_devices_empty boolean true",
            "acroread acroread/default-viewer boolean false",
            "rabbitmq-server rabbitmq-server/upgrade_previous note",
            ]
    cmd = ""
    for l in package_info:
        cmd += "echo %s | /usr/bin/debconf-set-selections ; " % l
    sudo(cmd)
