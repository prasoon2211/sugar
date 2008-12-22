# Copyright (C) 2008 Tomeu Vizoso
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import os
import logging
import socket

import gobject
import gio
import dbus
from telepathy.interfaces import CONNECTION_INTERFACE_REQUESTS, CHANNEL
from telepathy.constants import CONNECTION_HANDLE_TYPE_CONTACT,     \
                                SOCKET_ADDRESS_TYPE_UNIX,           \
                                SOCKET_ACCESS_CONTROL_LOCALHOST
from telepathy.client import Connection, Channel

from sugar.presence import presenceservice
from sugar import dispatch

from jarabe.util.telepathy import connection_watcher

FT_STATE_NONE = 0
FT_STATE_PENDING = 1
FT_STATE_ACCEPTED = 2
FT_STATE_OPEN = 3
FT_STATE_COMPLETED = 4
FT_STATE_CANCELLED = 5

# FIXME: use constants from tp-python once the spec is undrafted
CHANNEL_TYPE_FILE_TRANSFER = \
        'org.freedesktop.Telepathy.Channel.Type.FileTransfer.DRAFT'

class StreamSplicer(gobject.GObject):
    _CHUNK_SIZE = 102400 # 100K
    __gsignals__ = {
        'finished': (gobject.SIGNAL_RUN_FIRST,
                     gobject.TYPE_NONE,
                     ([])),
    }
    def __init__(self, input_stream, output_stream):
        gobject.GObject.__init__(self)

        self._input_stream = input_stream
        self._output_stream = output_stream
        self._pending_buffers = []

    def start(self):
        self._input_stream.read_async(self._CHUNK_SIZE, self.__read_async_cb,
                                      gobject.PRIORITY_LOW)

    def __read_async_cb(self, input_stream, result):
        data = input_stream.read_finish(result)
        #logging.debug('__read_async_cb %r' % len(data))
        if data:
            self._pending_buffers.append(data)
            if len(data) == self._CHUNK_SIZE:
                self._input_stream.read_async(self._CHUNK_SIZE,
                                              self.__read_async_cb,
                                              gobject.PRIORITY_LOW)

        if not data or len(data) < self._CHUNK_SIZE:
            logging.debug('closing input stream')
            self._input_stream.close()

        self._write_next_buffer()

    def __write_async_cb(self, output_stream, result, user_data):
        count_ = output_stream.write_finish(result)

        if not self._pending_buffers and \
                not self._output_stream.has_pending() and \
                not self._input_stream.has_pending():
            logging.debug('closing output stream')
            output_stream.close()
            self.emit('finished')
        else:
            self._write_next_buffer()            

    def _write_next_buffer(self):
        if self._pending_buffers and not self._output_stream.has_pending():
            data = self._pending_buffers.pop(0)
            # TODO: we pass the buffer as user_data because of
            # http://bugzilla.gnome.org/show_bug.cgi?id=564102
            self._output_stream.write_async(data, self.__write_async_cb,
                                            gobject.PRIORITY_LOW,
                                            user_data=data)

class BaseFileTransfer(gobject.GObject):

    def __init__(self, connection):
        gobject.GObject.__init__(self)
        self._connection = connection
        self._state = FT_STATE_NONE
        self._transferred_bytes = 0

        self.channel = None
        self.buddy = None
        self.title = None
        self.file_size = None
        self.description = None
        self.mime_type = None
        self.initial_offset = 0

    def set_channel(self, channel):
        self.channel = channel
        self.channel[CHANNEL_TYPE_FILE_TRANSFER].connect_to_signal(
                'FileTransferStateChanged', self.__state_changed_cb)
        self.channel[CHANNEL_TYPE_FILE_TRANSFER].connect_to_signal(
                'TransferredBytesChanged', self.__transferred_bytes_changed_cb)
        self.channel[CHANNEL_TYPE_FILE_TRANSFER].connect_to_signal(
                'InitialOffsetDefined', self.__initial_offset_defined_cb)

        channel_properties = self.channel[dbus.PROPERTIES_IFACE]

        props = channel_properties.GetAll(CHANNEL_TYPE_FILE_TRANSFER)
        self._state = props['State']
        self.title = props['Filename']
        self.file_size = props['Size']
        self.description = props['Description']
        self.mime_type = props['ContentType']

        handle = channel_properties.Get(CHANNEL, 'TargetHandle')
        presence_service = presenceservice.get_instance()
        self.buddy = presence_service.get_buddy_by_telepathy_handle(
                self._connection.service_name,
                self._connection.object_path,
                handle)

    def __transferred_bytes_changed_cb(self, transferred_bytes):
        logging.debug('__transferred_bytes_changed_cb %r' % transferred_bytes)
        self.props.transferred_bytes = transferred_bytes

    def _set_transferred_bytes(self, transferred_bytes):
        self._transferred_bytes = transferred_bytes

    def _get_transferred_bytes(self):
        return self._transferred_bytes

    transferred_bytes = gobject.property(type=int, default=0,
            getter=_get_transferred_bytes, setter=_set_transferred_bytes)

    def __initial_offset_defined_cb(self, offset):
        logging.debug('__initial_offset_defined_cb %r' % offset)
        self.initial_offset = offset

    def __state_changed_cb(self, state, reason):
        logging.debug('__state_changed_cb %r %r' % (state, reason))
        self.props.state = state

    def _set_state(self, state):
        self._state = state

    def _get_state(self):
        return self._state

    state = gobject.property(type=int, getter=_get_state, setter=_set_state)

    def cancel(self):
        self.channel[CHANNEL].Close()

class IncomingFileTransfer(BaseFileTransfer):
    def __init__(self, connection, object_path, props):
        BaseFileTransfer.__init__(self, connection)

        channel = Channel(connection.service_name, object_path)
        self.set_channel(channel)

        self.connect('notify::state', self.__notify_state_cb)

        self.destination_path = None
        self._socket_address = None
        self._socket = None
        self._splicer = None

    def accept(self, destination_path):
        if os.path.exists(destination_path):
            raise ValueError('Destination path already exists: %r' % \
                             destination_path)

        self.destination_path = destination_path

        channel_ft = self.channel[CHANNEL_TYPE_FILE_TRANSFER]
        self._socket_address = channel_ft.AcceptFile(SOCKET_ADDRESS_TYPE_UNIX,
                SOCKET_ACCESS_CONTROL_LOCALHOST, '', 0)

    def decline(self):
        self.channel[CHANNEL].Close()

    def __notify_state_cb(self, file_transfer, pspec):
        logging.debug('__notify_state_cb %r' % self.props.state)
        if self.props.state == FT_STATE_OPEN:
            # Need to hold a reference to the socket so that python doesn't
            # close the fd when it goes out of scope
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket.connect(self._socket_address)
            input_stream = gio.unix.InputStream(self._socket.fileno(), True)

            destination_file = gio.File(self.destination_path)
            if self.initial_offset == 0:
                output_stream = destination_file.create()
            else:
                output_stream = destination_file.append_to()

            # TODO: Use splice_async when it gets implemented
            self._splicer = StreamSplicer(input_stream, output_stream)
            self._splicer.start()

class OutgoingFileTransfer(BaseFileTransfer):
    def __init__(self, buddy, file_name, title, description, mime_type):

        presence_service = presenceservice.get_instance()
        name, path = presence_service.get_preferred_connection()
        connection = Connection(name, path,
                                ready_handler=self.__connection_ready_cb)

        BaseFileTransfer.__init__(self, connection)
        self.connect('notify::state', self.__notify_state_cb)

        self._file_name = file_name
        self._socket_address = None
        self._socket = None
        self._splicer = None
        self._output_stream = None

        self.buddy = buddy.get_buddy()
        self.title = title
        self.file_size = os.stat(file_name).st_size
        self.description = description
        self.mime_type = mime_type

    def __connection_ready_cb(self, connection):
        handle = self._get_buddy_handle()

        requests = connection[CONNECTION_INTERFACE_REQUESTS]
        object_path, properties_ = requests.CreateChannel({
            CHANNEL + '.ChannelType': CHANNEL_TYPE_FILE_TRANSFER,
            CHANNEL + '.TargetHandleType': CONNECTION_HANDLE_TYPE_CONTACT,
            CHANNEL + '.TargetHandle': handle,
            CHANNEL_TYPE_FILE_TRANSFER + '.ContentType': self.mime_type,
            CHANNEL_TYPE_FILE_TRANSFER + '.Filename': self.title,
            CHANNEL_TYPE_FILE_TRANSFER + '.Size': self.file_size,
            CHANNEL_TYPE_FILE_TRANSFER + '.Description': self.description,
            CHANNEL_TYPE_FILE_TRANSFER + '.InitialOffset': 0})

        self.set_channel(Channel(connection.service_name, object_path))
        
        channel_file_transfer = self.channel[CHANNEL_TYPE_FILE_TRANSFER]
        self._socket_address = channel_file_transfer.ProvideFile(
                SOCKET_ADDRESS_TYPE_UNIX, SOCKET_ACCESS_CONTROL_LOCALHOST, '')

    def _get_buddy_handle(self):
        object_path = self.buddy.object_path()

        bus = dbus.SessionBus()
        remote_object = bus.get_object('org.laptop.Sugar.Presence', object_path)
        ps_buddy = dbus.Interface(remote_object,
                                  'org.laptop.Sugar.Presence.Buddy')

        handles = ps_buddy.GetTelepathyHandles()
        logging.debug('_get_buddy_handle %r' % handles)

        bus_name, object_path, handle = handles[0]

        return handle

    def __notify_state_cb(self, file_transfer, pspec):
        logging.debug('__notify_state_cb %r' % self.props.state)
        if self.props.state == FT_STATE_OPEN:
            # Need to hold a reference to the socket so that python doesn't
            # closes the fd when it goes out of scope
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket.connect(self._socket_address)
            output_stream = gio.unix.OutputStream(self._socket.fileno(), True)

            logging.debug('opening %s for reading' % self._file_name)
            input_stream = gio.File(self._file_name).read()
            if self.initial_offset > 0:
                input_stream.skip(self.initial_offset)

            # TODO: Use splice_async when it gets implemented
            self._splicer = StreamSplicer(input_stream, output_stream)
            self._splicer.start()

    def cancel(self):
        self.channel[CHANNEL].Close()

def _new_channels_cb(connection, channels):
    for object_path, props in channels:
        if props[CHANNEL + '.ChannelType'] == CHANNEL_TYPE_FILE_TRANSFER and \
                not props[CHANNEL + '.Requested']:

            logging.debug('__new_channels_cb %r' % object_path)

            incoming_file_transfer = IncomingFileTransfer(connection,
                                                          object_path, props)
            new_file_transfer.send(None, file_transfer=incoming_file_transfer)

def _monitor_connection(connection):
    connection[CONNECTION_INTERFACE_REQUESTS].connect_to_signal('NewChannels',
            lambda channels: _new_channels_cb(connection, channels))

def _connection_addded_cb(conn_watcher, connection):
    _monitor_connection(connection)

def init():
    conn_watcher = connection_watcher.ConnectionWatcher()
    conn_watcher.connect('connection-added', _connection_addded_cb)

    for connection in conn_watcher.get_connections():
        _monitor_connection(connection)

def start_transfer(buddy, file_name, title, description, mime_type):
    outgoing_file_transfer = OutgoingFileTransfer(buddy, file_name, title,
                                                  description, mime_type)
    new_file_transfer.send(None, file_transfer=outgoing_file_transfer)

new_file_transfer = dispatch.Signal()
