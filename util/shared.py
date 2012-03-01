""" Shared functionality useful for multiple package managers.
    Code adapted from Brad Chapman and https://github.com/chapmanb/cloudbiolinux
"""
import yaml
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
