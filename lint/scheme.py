import json
import os
from abc import ABCMeta, abstractmethod
from xml.etree import ElementTree
from copy import deepcopy

import sublime
import re
from . import util


MARK_COLOR_RE = (
    r'(\s*<string>sublimelinter\.{}</string>\s*\r?\n'
    r'\s*<key>settings</key>\s*\r?\n'
    r'\s*<dict>\s*\r?\n'
    r'(?:\s*<key>(?:background|fontStyle)</key>\s*\r?\n'
    r'\s*<string>.*?</string>\r?\n)*'
    r'\s*<key>foreground</key>\s*\r?\n'
    r'\s*<string>)#.+?(</string>\s*\r?\n)'
)

COLOR_SCHEME_PREAMBLE = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
'''
DYN_PAT = re.compile("sublimelinter\.\w+?\.style_\d{3,}")


class Scheme(metaclass=ABCMeta):
    """This class provides global access to scheme editing."""

    def __init__(self):
        """ """
        self.static_nodes = {}  # with predefined scope name
        self.dynamic_nodes = {}  # for

        self.prefs = {}
        self.scheme = ""  # later include into self.paths

        self.paths = {}

    def generate(self, from_reload=True):
        """
        Asynchronously call generate_color_scheme_async.

        from_reload is True if this is called from the change callback for user settings.

        """

        # If this was called from a reload of prefs, turn off the prefs observer,
        # otherwise we'll end up back here when ST updates the prefs with the new color.

        from . import style
        style_parser = style.StyleParser()
        style_parser.create_styles()

        self.update_paths()

        # First make sure the user prefs are valid. If not, bail.
        self.get_settings()
        if not (self.prefs and self.scheme):
            return

        # self.update_paths()

        if from_reload:
            from . import persist

            def prefs_reloaded():
                persist.settings.observe_prefs()

            persist.settings.observe_prefs(observer=prefs_reloaded)

        # ST crashes unless this is run async
        sublime.set_timeout_async(self.generate_color_scheme_async, 0)

    def get_settings(self):
        """Return preference object and color scheme """
        settings_path = os.path.join(
            self.paths["usr_dir"], 'Preferences.sublime-settings')

        # TODO: couldn't better use sublime.load_resource ??
        if (os.path.isfile(settings_path)):
            try:
                with open(settings_path, mode='r', encoding='utf-8') as f:
                    json = f.read()
                sublime.decode_value(json)
            except:
                from . import persist
                util.printf(
                    'generate_color_scheme: Preferences.sublime-settings invalid, aborting'
                )
                return

        # TODO: or take prefs from persist?
        self.prefs = sublime.load_settings('Preferences.sublime-settings')
        self.scheme = self.prefs.get('color_scheme')
        self.paths["scheme_orig"] = self.get_original_theme(self.scheme)
        self.paths["scheme_base"] = os.path.basename(self.paths["scheme_orig"])
        self.paths["scheme_name"], self.paths["ext"] = os.path.splitext(
            self.paths["scheme_base"])

        self.paths["usr_dir_rel"] = os.path.join("User", "SublimeLinter")
        self.paths["usr_dir_abs"] = os.path.join(
            sublime.packages_path(), self.paths["usr_dir_rel"])

    def get_original_theme(self, current_scheme_path):
        current_scheme_file = current_scheme_path.split("/")[-1]
        pattern = re.sub(r" ?\(SL\) ?|hidden-", "", current_scheme_file)

        theme_list = sublime.find_resources(pattern)

        if theme_list:
            theme_list = [t for t
                          in theme_list
                          if "Packages/User/" not in t
                          ]

        if not theme_list:
            return current_scheme_path

        return theme_list[0]

    def update_paths(self):

        pck_dir = sublime.packages_path()

        self.paths.update({
                          "usr_dir": os.path.join(pck_dir, "User")
                          })

    def parse_scheme_xml(self, nodes, *, text):
        """ included in base class as used by both derived classes, despite 'XML'"""
        unfound_nodes = []

        for scope in nodes:
            match = re.search(MARK_COLOR_RE.format(
                re.escape(scope)),
                text)
            if not match:
                unfound_nodes.append(scope)

        return unfound_nodes

    @staticmethod
    def touch_dir(dir):
        """Create dir if it does not exist."""
        # TODO: need to make it recursive???
        if not os.path.exists(dir):
            os.makedirs(dir)

    def set_scheme_path(self, path):
        """Set 'color_scheme' to provided path if it is currently is not."""
        from . import persist
        print("path: ", path)
        print("self.scheme: ", self.scheme)

        if path != self.scheme:
            util.printf("New scheme path detected. Updating.")
            self.prefs.set('color_scheme', path)
            sublime.save_settings('Preferences.sublime-settings')
        else:
            util.printf("Old scheme path detected. Pass.")
            pass

    def unfound_scopes_dialogue(self, unfound):
        from . import persist
        msg = "The following scopes have not been found in the color scheme:\n{}".format(
            "\n".join(unfound))
        util.printf(msg)

    @abstractmethod
    def generate_color_scheme_async(self):
        """       """
        pass

    @abstractmethod
    def assemble_node(self, scope, input_dict, name=None):
        """"""
        pass


class XmlScheme(Scheme):
    """docstring for XmlScheme"""

    def generate_color_scheme_async(self):
        """
            Generate a modified copy of the current color scheme that contains SublimeLinter color entries.

            The current color scheme is checked for SublimeLinter color entries. If any are missing,
            the scheme is copied, the entries are added, and the color scheme is rewritten to Packages/User/SublimeLinter.

            """
        print("XmlScheme.generate_color_scheme called.")

        # Append style dicts with our styles to the style array
        scheme_text = sublime.load_resource(self.paths["scheme_orig"])
        plist = ElementTree.XML(scheme_text)
        styles = plist.find('./dict/array')
        styles.extend(self.dynamic_nodes.values())

        from . import persist

        unfound = self.parse_scheme_xml(
            self.static_nodes.keys(), text=scheme_text)
        if unfound:
            self.unfound_scopes_dialogue(unfound)

        print("self.paths['usr_dir_abs']: ", self.paths["usr_dir_abs"])

        mod_name = self.paths["scheme_name"] + ' (SL)'
        mod_scheme_path = os.path.join(
            self.paths["usr_dir_abs"], mod_name + '.hidden-tmTheme')

        content = ElementTree.tostring(plist, encoding='unicode')

        with open(mod_scheme_path, 'w', encoding='utf8') as f:
            f.write(COLOR_SCHEME_PREAMBLE)
            f.write(content)

        # Set the amended color scheme to the current color scheme
        scheme_path_rel = self.packages_relative_path(
            os.path.join(self.paths["usr_dir_rel"], os.path.basename(mod_scheme_path)))

        # TODO: is there another way to prevent entering vicious cycle?
        self.set_scheme_path(scheme_path_rel)

    def assemble_node(self, scope, input_dict, name=None):
        """Assembles single node as XML ElementTree object."""
        root = ElementTree.Element('dict')

        def append_kv(first, second, root=root):
            ElementTree.SubElement(root, 'key').text = first
            ElementTree.SubElement(root, 'string').text = second

        if not name:
            name = scope

        append_kv("name", name)
        append_kv("scope", scope)
        ElementTree.SubElement(root, "key").text = "settings"
        d = ElementTree.SubElement(root, "dict")

        if input_dict.get("foreground"):
            append_kv("foreground", input_dict.get("foreground").upper(), d)

        if input_dict.get("background"):
            append_kv("background", input_dict.get("background").upper(), d)

        if input_dict.get("font_style"):
            append_kv("fontStyle", input_dict.get("font_style"), d)

        if re.match(DYN_PAT, scope):
            self.dynamic_nodes[scope] = root
        else:
            self.static_nodes[scope] = root

    def packages_relative_path(self, path, prefix_packages=True):
        """
        Return a Packages-relative version of path with '/' as the path separator.

        Sublime Text wants Packages-relative paths used in settings and in the plugin API
        to use '/' as the path separator on all platforms. This method converts platform
        path separators to '/'. If insert_packages = True, 'Packages' is prefixed to the
        converted path.

        """
        from . import util

        components = util.get_path_components(path)

        if prefix_packages and components and components[0] != 'Packages':
            components.insert(0, 'Packages')

        return '/'.join(components)


class JsonScheme(Scheme):

    def generate_color_scheme_async(self):
        """Generates scheme in format .subilme-color-scheme."""
        print("JsonScheme.generate_color_scheme called.")
        original_scheme = self.get_original_theme(self.scheme)
        scheme_text = sublime.load_resource(original_scheme)

        print("type of dict l284 in scheme.py: ", type(scheme_text))

        if self.paths["ext"] == ".sublime-color-scheme":
            unfound = self.parse_scheme_json(
                self.static_nodes, rules=scheme_text.get("rules", {}))
        elif self.paths["ext"].endswith("tmTheme"):

            unfound = self.parse_scheme_xml(
                self.static_nodes, text=scheme_text)
        else:  # file extension not defined
            raise Exception

        if not unfound and not self.dynamic_nodes:
            print("No nodes to include")
            return

        new_scheme_path = os.path.join(self.paths["usr_dir"],
                                       self.paths["scheme_name"] +
                                       ".sublime-color-scheme"
                                       )

        theme = {"rules": []}
        if os.path.exists(new_scheme_path):
            with open(new_scheme_path, "r") as f:
                print("new_scheme_path exists")
                theme = json.load(f)

            old_rules = deepcopy(theme.get("rules"))

            theme["rules"].clear()
            if old_rules:
                if unfound:
                    unfound = self.parse_scheme_json(unfound, rules=old_rules)
                    if unfound:
                        self.unfound_scopes_dialogue(unfound)

                cleaned_rules = self.remove_dyn_rules(old_rules)
                theme["rules"].extend(cleaned_rules)

        theme["rules"].extend(self.dynamic_nodes.values())

        with open(new_scheme_path, "w") as f:
            content = json.dumps(theme, indent=4, sort_keys=True)
            f.write(content)

        print("self.scheme: ", self.scheme)
        print("self.paths[\"scheme_orig\"]: ", self.paths["scheme_orig"])

        # To ensure update when theme set to 'xxx (SL).tmTheme'
        self.set_scheme_path(self.paths["scheme_orig"])

    def parse_scheme_json(self, nodes, *, rules):
        """Returns dict of {scope: style} not defined in json."""
        unfound_keys = set(nodes)

        for node in rules:
            def_scopes = node.get("scope", "").split()
            unfound_keys -= set(def_scopes)  # remove existing scopes
            if not unfound_keys:  # if all scopes found return
                return {}

        return {k: v for k, v in nodes.items() if k in unfound_keys}

    def assemble_node(self, scope, input_dict, name=None):

        if not name:
            name = scope

        husk = {"scope": scope, "name": name}

        used_keys = ("foreground", "background", "font_style")
        for k, v in input_dict.items():
            if k in used_keys:
                husk[k] = v

        if re.match(DYN_PAT, scope):
            self.dynamic_nodes[scope] = husk
        else:
            self.static_nodes[scope] = husk

    def remove_dyn_rules(self, rules):
        """Remove dynamic style defintions."""
        # sublimelinter\.\w+?\.style_\d{3,}
        result_dict = []
        for r in rules:
            if "scope" in r:
                if not re.search(DYN_PAT, r["scope"]):
                    result_dict.append(r)
        return result_dict


def init_scheme(force_xml_scheme=False):
    """Returns either the modern json schme parser or old xml scheme parser. Depending on sublime version."""
    if int(sublime.version()) >= 3149 and not force_xml_scheme:
        return JsonScheme()

    return XmlScheme()
