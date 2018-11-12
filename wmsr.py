#!/usr/bin/env python3

import ewmh
import json
import os
import sys
import Xlib

from pprint import pformat


class WmDisplay(object):
    """
    Utility wrapper around Xlib.display.Display
    :type display: Xlib.display.Display
    """
    display = None

    @classmethod
    def get(cls):
        if cls.display is None:
            cls.display = Xlib.display.Display()
        return cls.display

    @classmethod
    def get_window(cls, id):
        """
        :rtype Xlib.display.Window
        """
        win = cls.get().create_resource_object('window', id)
        # Check if the window is valid
        try:
            win.get_wm_class()
        except Xlib.error.BadWindow:
            return None

        return win

    @classmethod
    def get_frame(cls, client, root):
        frame = client
        while frame.query_tree().parent != root:
            frame = frame.query_tree().parent
        return frame


class WmWindow(object):
    """
    Representation of an application window
    """
    MAXIMIZED_VERT = '_NET_WM_STATE_MAXIMIZED_VERT'
    MAXIMIZED_HORZ = '_NET_WM_STATE_MAXIMIZED_HORZ'
    MAXIMIZED_STATES = {MAXIMIZED_VERT, MAXIMIZED_HORZ}

    SKIP_ATTR = ['win']

    def __init__(self, win=None, ew=None):
        """
        :type win: Xlib.display.Window
        :type ew: ewmh.EWMH
        """
        self.win = None
        self.id = None
        self.type = []
        self.state = {}
        self.geometry = []

        if win is not None:
            self.win = win
            self.id = win.id
            self.type = ew.getWmWindowType(win, True)
            self.state = set(ew.getWmState(win, True))
            self.name = ew.getWmName(win).decode('utf-8')
            self.geometry = self.get_geometry(ew.root)

    def get_geometry(self, root):
        geo_w = self.win.get_geometry()
        geo_f = WmDisplay.get_frame(self.win, root).get_geometry()
        return [geo_f.x, geo_f.y, geo_w.width, geo_w.height]

    @staticmethod
    def serializer(obj):
        """
        :type obj: WmWindow
        :return: dict
        """
        if isinstance(obj, WmWindow):
            data = {}
            for key in obj.__dict__:
                if key not in WmWindow.SKIP_ATTR:
                    data[key] = obj.__dict__[key]
            return data

        if isinstance(obj, set):
            return list(obj)

        raise TypeError("Cannot get serializable version of object:\n{}".format(pformat(obj)))

    def move(self, geometry, ew, state=None):
        """
        Move the window
        :type geometry: Tuple
        :type ew: ewmh.EWMH
        :type state: set
        :rtype: None
        """
        if geometry == self.geometry:
            print('{}: same geometry, not moving'.format(self.id))
            return

        states = self.state.intersection(self.MAXIMIZED_STATES)

        if len(states) > 0:
            print('{}: removing [{}]'.format(self.id, ', '.join(states)))
            states_list = list(states) + [0]
            ew.setWmState(self.win, WmWindowPersister.ACTION_REMOVE, states_list[0], states_list[1])

        x, y, w, h = geometry
        print('{}: moving to {}, {}, {}, {}'.format(self.id, x, y, w, h))
        ew.setMoveResizeWindow(self.win, x=x, y=y, w=w, h=h)

        if state is not None:
            states = state.intersection(self.MAXIMIZED_STATES)

        if len(states) > 0:
            print('{}: restoring [{}]'.format(self.id, ', '.join(states)))
            states_list = list(states) + [0]
            ew.setWmState(self.win, WmWindowPersister.ACTION_ADD, states_list[0], states_list[1])


class WmWindowPersister(object):
    """
    :type windows: dict of WmWindow
    """
    ACTION_REMOVE = 0
    ACTION_ADD = 1
    ACTION_TOGGLE = 2

    def __init__(self):
        self.ew = ewmh.EWMH()
        self.windows = None

    def get_windows(self, filter=True):
        self.windows = {}
        wins = self.ew.getClientList()
        for win in wins:
            w = WmWindow(win, self.ew)
            if not filter or (len(w.type) == 1 and w.type[0] == '_NET_WM_WINDOW_TYPE_NORMAL'):
                self.windows[win.id] = w

    def dumps(self):
        try:
            return json.dumps(self.windows, default=WmWindow.serializer, indent=2)
        except TypeError as ex:
            print('Error encoding data: {}'.format(ex))

    def save(self, filename, reload=False):
        """
        Save the current window state to a file
        :type filename: str
        :type reload: bool
        :rtype: bool
        """
        if self.windows is None or reload:
            self.get_windows()

        data = self.dumps()
        if data is None:
            return False

        try:
            with open(filename, 'w') as fp:
                fp.write(data)
                print('Saved state of {} windows.'.format(len(self.windows)))
                return True
        except IOError as ex:
            print('Error writing to {}: {}'.format(filename, ex))

        return False

    @staticmethod
    def read_data(filename):
        try:
            with open(filename, 'r') as fp:
                windows = json.load(fp)
        except IOError as ex:
            print('Error reading from {}: {}'.format(filename, ex))
            return None
        except json.decoder.JSONDecodeError as ex:
            print('Error decoding data: {}', ex)
            return None
        return windows

    def restore(self, filename, reload=False):
        windows = self.read_data(filename)
        if windows is None:
            return False

        print('Loaded state of {} windows'.format(len(windows)))

        if self.windows is None or reload:
            self.get_windows()

        for id in windows:
            win = windows[id]
            iid = int(id)
            if iid not in self.windows:
                # Window doesn't exist any more, skip
                print('{}: not found, skipping'.format(id))
                continue
            cur_win = self.windows[iid]
            cur_win.move(win['geometry'], self.ew, set(win['state']))
        self.ew.display.flush()


DEFAULT_STATE_PATH = os.path.join(os.environ['HOME'], '.windows.json')


def usage():
    print('Usage: wmsr.py save|restore [STATE_FILE]')
    print('Save/restore the size/position/state of windows')
    print('STATE_FILE defaults to {}'.format(DEFAULT_STATE_PATH))


def get_options():
    state_path = DEFAULT_STATE_PATH
    args = sys.argv
    args.pop(0)
    if len(args) < 1 or len(args) > 2:
        return False, False

    action = args.pop(0)
    if action not in ['save', 'restore']:
        return False, False

    if len(args) > 0:
        state_path = args[0]

    return action, state_path


if __name__ == "__main__":
    action, state_file_path = get_options()
    if not action:
        usage()
        sys.exit(1)

    wmsr = WmWindowPersister()
    if action == 'save':
        print('Saving state of all windows...')
        wmsr.save(state_file_path)
    else:
        print('Restoring state of all windows...')
        wmsr.restore(state_file_path)
