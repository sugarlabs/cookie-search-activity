# Copyright (c) 2011 Walter Bender

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('TelepathyGLib', '0.12')
from gi.repository import Gtk, Gdk
from gi.repository import TelepathyGLib

from sugar3.activity import activity
from sugar3 import profile
from sugar3.graphics.toolbarbox import ToolbarBox
from sugar3.activity.widgets import ActivityToolbarButton
from sugar3.activity.widgets import StopButton

from toolbar_utils import button_factory, label_factory, separator_factory
from utils import json_load, json_dump, convert_seconds_to_minutes

# import telepathy
# import dbus
# from dbus.service import signal
# from dbus.gobject_service import ExportedGObject
# from sugar3.presence import presenceservice
# from sugar3.presence.tubeconn import TubeConnection

from collabwrapper import CollabWrapper
from gettext import gettext as _

import json
from json import load as jload
from json import dump as jdump

from io import StringIO

from game import Game

import logging
_logger = logging.getLogger('cookie-search-activity')


SERVICE = 'org.sugarlabs.CookieSearchActivity'
IFACE = SERVICE
PATH = '/org/sugarlabs/CookieSearchActivity'

class SearchActivity(activity.Activity):
    """ Searching strategy game """

    def __init__(self, handle):
        """ Initialize the toolbars and the game board """
        super(SearchActivity, self).__init__(handle)

        self.path = activity.get_bundle_path()
        self.all_scores = []

        self.nick = profile.get_nick_name()
        if profile.get_color() is not None:
            self.colors = profile.get_color().to_string().split(',')
        else:
            self.colors = ['#A0FFA0', '#FF8080']

        self._setup_toolbars()
        self._setup_dispatch_table()

        # Create a canvas
        canvas = Gtk.DrawingArea()
        canvas.set_size_request(Gdk.Screen.width(),
                                Gdk.Screen.height())
        self.set_canvas(canvas)
        canvas.show()
        self.show_all()

        self._game = Game(canvas, parent=self, path=self.path,
                          colors=self.colors)

        self.connect('shared', self._shared_cb)
        self.connect('joined', self._joined_cb)

        self._collab = CollabWrapper(self)
        self._collab.connect('message', self._message_cb)
        self._collab.connect('joined', self._joined_cb)
        self._collab.setup()

        if 'dotlist' in self.metadata:
            self._restore()
        else:
            self._game.new_game()

    def _setup_toolbars(self):
        """ Setup the toolbars. """

        self.max_participants = 4

        toolbox = ToolbarBox()

        # Activity toolbar
        activity_button = ActivityToolbarButton(self)

        toolbox.toolbar.insert(activity_button, 0)
        activity_button.show()

        self.set_toolbar_box(toolbox)
        toolbox.show()
        self.toolbar = toolbox.toolbar

        export_scores = button_factory(
            'score-copy',
            activity_button,
            self._write_scores_to_clipboard,
            tooltip=_('Export scores to clipboard'))

        self._new_game_button_h = button_factory(
            'new-game',
            self.toolbar,
            self._new_game_cb,
            tooltip=_('Start a new game.'))

        self.status = label_factory(self.toolbar, '', width=300)

        separator_factory(toolbox.toolbar, True, False)

        stop_button = StopButton(self)
        toolbox.toolbar.insert(stop_button, -1)
        stop_button.show()

    def _new_game_cb(self, button=None):
        ''' Start a new game. '''
        self._game.new_game()


    def write_file(self, file_path):
        """ Write the grid status to the Journal """
        dot_list = self._game.save_game()
        self.metadata['dotlist'] = ''
        for dot in dot_list:
            self.metadata['dotlist'] += str(dot)
            if dot_list.index(dot) < len(dot_list) - 1:
                self.metadata['dotlist'] += ' '

        self.metadata['all_scores'] = \
            self._data_dumper(self.all_scores)
        self.metadata['current_gametime'] = self._game._game_time_seconds
        self.metadata['current_level'] = self._game.level

    def _data_dumper(self, data):
        io = StringIO()
        jdump(data, io)
        return io.getvalue()

    def _restore(self):
        """ Restore the game state from metadata """
        if 'current_gametime' in self.metadata:
            # '-1' Workaround for showing last second
            self._game._game_time_seconds = self._data_loader(
                self.metadata['current_gametime']) - 1
        else:
            self._game._game_time_seconds = 0;
        self._game._game_time = convert_seconds_to_minutes(
            self._game._game_time_seconds)

        if 'current_level' in self.metadata:
            self._game.level = self._data_loader(self.metadata['current_level'])

        if 'dotlist' in self.metadata:
            dot_list = []
            dots = self.metadata['dotlist'].split()
            for dot in dots:
                dot_list.append(int(dot))

        self._game.restore_game(dot_list)

        if 'all_scores' in self.metadata:
            self.all_scores = self._data_loader(self.metadata['all_scores'])
        else:
            self.all_scores = []
        _logger.debug(self.all_scores)

    def _data_loader(self, data):
        io = StringIO(data)
        return jload(io)

    def _write_scores_to_clipboard(self, button=None):
        ''' SimpleGraph will plot the cululative results '''
        _logger.debug(self.all_scores)
        scores = ''
        for i, s in enumerate(self.all_scores):
            scores += '{}: {}\n'.format(str(i + 1), s)
        Gtk.Clipboard().set_text(scores)
        

    # Collaboration-related methods
    
    def set_data(self, data):
        pass

    def get_data(self):
        return None

    def _shared_cb(self, activity):
        """ Either set up initial share..."""
        self.after_share_join(True)

    def _joined_cb(self, activity):
        """ ...or join an exisiting share. """
        self.after_share_join(False)

    def after_share_join(self, sharer):
        self.waiting_for_hand = not sharer
        self._game.set_sharing(True)

    def _setup_dispatch_table(self):
        ''' Associate tokens with commands. '''
        self._processing_methods = {
            'n': [self._receive_new_game, 'get a new game grid'],
            'p': [self._receive_dot_click, 'get a dot click'],
        }

    def _message_cb(self, collab, buddy, msg):
        ''' Data from a tube has arrived. '''
        command = msg.get('command')
        payload = msg.get('payload')
        self._processing_methods[command][0](payload)

    def send_new_game(self):
        ''' Send a new grid to all players '''
        print('sending game..')
        self.send_event('n', self._game.save_game())

    def _receive_new_game(self, payload):
        ''' Sharer can start a new game. '''
        print('recieved game. payload length: {}'.format(len(payload)))
        dot_list = payload
        self._game.restore_game(dot_list)

    def send_dot_click(self, dot, color):
        ''' Send a dot click to all the players '''
        print('sending dot click, for dot: {} and color: {}'.format(dot, color))
        self.send_event('p', json_dump([dot, color]))
        # self.send_event('p|{}'.format(json_dump([dot, color])))
        
    def _receive_dot_click(self, payload):
        ''' When a dot is clicked, everyone should change their color. '''
        (dot, color) = json_load(payload)
        print('recieved dot click, for dot: {} and color: {}'.format(dot, color))
        self._game.remote_button_press(dot, color)

    def send_event(self, command, payload):
        """ Send event through the tube. """
        self._collab.post({'command': command, 'payload': payload})