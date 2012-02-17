#!/usr/bin/env python

'''Effective seismological trace viewer.'''

import os, sys, signal, logging, time, re, gc
import numpy as num

import pyrocko.pile
import pyrocko.util
import pyrocko.pile_viewer
import pyrocko.model

from PyQt4.QtCore import *
from PyQt4.QtGui import *

logger = logging.getLogger('pyrocko.snuffler')

class Connection(QObject):
    def __init__(self, parent, sock):
        QObject.__init__(self, parent)
        self.socket = sock
        self.connect(sock, SIGNAL('readyRead()'), self.handle_read)
        self.connect(sock, SIGNAL('disconnected()'), self.handle_disconnected)
        self.nwanted = 8
        self.reading_size = True
        self.handler = None
        self.nbytes_received = 0
        self.nbytes_sent = 0
        self.compressor = zlib.compressobj()
        self.decompressor = zlib.decompressobj()

    def handle_read(self):
        while True:
            navail = self.socket.bytesAvailable()
            if navail < self.nwanted:
                return

            data = self.socket.read(self.nwanted)
            self.nbytes_received += len(data)
            if self.reading_size:
                self.nwanted = struct.unpack('>Q', data)[0]
                self.reading_size = False
            else:
                obj = pickle.loads(self.decompressor.decompress(data))
                if obj is None:
                    self.socket.disconnectFromHost()
                else:
                    self.handle_received(obj)
                self.nwanted = 8
                self.reading_size = True

    def handle_received(self, obj):
        self.emit(SIGNAL('received(PyQt_PyObject,PyQt_PyObject)'), self, obj)

    def ship(self, obj):
        data = self.compressor.compress(pickle.dumps(obj))
        data_end = self.compressor.flush(zlib.Z_FULL_FLUSH)
        self.socket.write(struct.pack('>Q', len(data)+len(data_end)))
        self.socket.write(data)
        self.socket.write(data_end)
        self.nbytes_sent += len(data)+len(data_end) + 8

    def handle_disconnected(self):
        self.emit(SIGNAL('disconnected(PyQt_PyObject)'), self)

    def close(self):
        self.socket.close()

class ConnectionHandler(QObject):
    def __init__(self, parent):
        QObject.__init__(self, parent)
        self.queue = []
        self.connection = None

    def connected(self):
        return self.connection == None

    def set_connection(self, connection):
        self.connection = connection
        self.connect(connection, SIGNAL('received(PyQt_PyObject,PyQt_PyObject)'), self._handle_received)
        self.connect(connection, SIGNAL('disconnected(PyQt_PyObject)'), self.handle_disconnected)
        for obj in self.queue:
            self.connection.ship(obj)
        self.queue = []

    def _handle_received(self, conn, obj):
        self.handle_received(obj)

    def handle_received(self, obj):
        pass

    def handle_disconnected(self):
        self.connection = None

    def ship(self, obj):
        if self.connection:
            self.connection.ship(obj)
        else:
            self.queue.append(obj)

class SimpleConnectionHandler(ConnectionHandler):
    def __init__(self, parent, **mapping):
        ConnectionHandler.__init__(self, parent)
        self.mapping = mapping

    def handle_received(self, obj):
        command = obj[0]
        args = obj[1:]
        self.mapping[command](*args)


class MyMainWindow(QMainWindow):

    def __init__(self, app, *args):
        QMainWindow.__init__(self, *args)
        self.app = app

    def keyPressEvent(self, ev):
        self.app.pile_viewer.get_view().keyPressEvent(ev)


class SnufflerTabs(QTabWidget):
    def __init__(self, parent):
        QTabWidget.__init__(self, parent)
        if hasattr(self, 'setTabsClosable'):
            self.setTabsClosable(True)
        self.connect(self, SIGNAL('tabCloseRequested(int)'), self.removeTab)
        if hasattr(self, 'setDocumentMode'):
            self.setDocumentMode(True)

    def hide_close_button_on_first_tab(self):
        tbar = self.tabBar()
        if hasattr(tbar ,'setTabButton'):
            tbar.setTabButton(0, QTabBar.LeftSide, None)
            tbar.setTabButton(0, QTabBar.RightSide, None)

    def append_tab(self, widget, name):
        widget.setParent(self)
        self.insertTab(self.count(), widget, name)
        self.setCurrentIndex(self.count()-1)

    def tabInserted(self, index):
        if index == 0:
            self.hide_close_button_on_first_tab()

        self.tabbar_visibility()

    def tabRemoved(self, index):
        self.tabbar_visibility()

    def tabbar_visibility(self):
        if self.count() <= 1:
            self.tabBar().hide()
        elif self.count() > 1:
            self.tabBar().show()

class SnufflerWindow(QMainWindow):

    def __init__(self, pile, stations=None, events=None, markers=None, 
                        ntracks=12, follow=None, controls=True, opengl=False):
        
        QMainWindow.__init__(self)

        self.dockwidget_to_toggler = {}
            
        self.setWindowTitle( "Snuffler" )        

        self.pile_viewer = pyrocko.pile_viewer.PileViewer(
            pile, ntracks_shown_max=ntracks, use_opengl=opengl, panel_parent=self)
       
        if stations:
            self.pile_viewer.get_view().add_stations(stations)
       
        if events:
            for ev in events:
                self.pile_viewer.get_view().add_event(ev)
            
            self.pile_viewer.get_view().set_origin(events[0])

        if markers:
            self.pile_viewer.get_view().add_markers(markers)

        
        self.tabs = SnufflerTabs(self)
        self.setCentralWidget( self.tabs )
        self.add_tab('Main', self.pile_viewer)

        self.pile_viewer.setup_snufflings()

        self.add_panel('Main Controls', self.pile_viewer.controls(), visible=controls)
        self.show()

        self.pile_viewer.get_view().setFocus(Qt.OtherFocusReason)

        sb = self.statusBar()
        sb.clearMessage()
        sb.showMessage('Welcome to Snuffler! Click and drag to zoom and pan. Doubleclick to pick. Right-click for Menu. <space> to step forward. <b> to step backward. <q> to close.')
            
        if follow:
            self.pile_viewer.get_view().follow(float(follow))

    def dockwidgets(self):
        return [ w for w in self.findChildren(QDockWidget) if not w.isFloating() ]

    def get_panel_parent_widget(self):
        return self

    def add_tab(self, name, widget):
        self.tabs.append_tab(widget, name)

    def add_panel(self, name, panel, visible=False, volatile=False):
        dws = self.dockwidgets()
        dockwidget = QDockWidget(name, self)
        dockwidget.setWidget(panel)
        panel.setParent(dockwidget)
        self.addDockWidget(Qt.BottomDockWidgetArea, dockwidget)

        if dws:
            self.tabifyDockWidget(dws[-1], dockwidget)
        
        self.toggle_panel(dockwidget, visible)

        mitem = QAction(name, None)
        
        def toggle_panel(checked):
            self.toggle_panel(dockwidget, True)

        self.connect( mitem, SIGNAL('triggered(bool)'), toggle_panel)

        if volatile:
            def visibility(visible):
                if not visible:
                    self.remove_panel(panel)

            self.connect( dockwidget, SIGNAL('visibilityChanged(bool)'), visibility)

        self.pile_viewer.get_view().add_panel_toggler(mitem)

        self.dockwidget_to_toggler[dockwidget] = mitem


    def toggle_panel(self, dockwidget, visible):
        dockwidget.setVisible(visible)
        if visible:
            dockwidget.setFocus()
            dockwidget.raise_()

    def remove_panel(self, panel):
        dockwidget = panel.parent()
        self.removeDockWidget(dockwidget)
        dockwidget.setParent(None)
        mitem = self.dockwidget_to_toggler[dockwidget]
        self.pile_viewer.get_view().remove_panel_toggler(mitem)
        
    def return_tag(self):
        return self.pile_viewer.get_view().return_tag
    
class Snuffler(QApplication):
    
    def __init__(self):
        QApplication.__init__(self, [])
        self.connect(self, SIGNAL("lastWindowClosed()"), self.myQuit)
        signal.signal(signal.SIGINT, self.myCloseAllWindows)
        self.server = None
        self.loader = None

    def start_server(self):
        self.connections = []
        s = QTcpServer(self)
        s.listen(QHostAddress.LocalHost)
        self.connect(s, SIGNAL('newConnection()'), self.handle_accept)
        self.server = s

    def start_loader(self):
        self.loader = SimpleConnectionHandler(self, add_files=self.add_files, update_progress=self.update_progress)
        ticket = os.urandom(32)
        self.forker.spawn('loader', self.server.serverPort(), ticket)
        self.connection_handlers[ticket] = self.loader

    def handle_accept(self):
        sock = self.server.nextPendingConnection()
        con = Connection(self, sock)
        self.connections.append(con)
        self.connect(con, SIGNAL('disconnected(PyQt_PyObject)'), self.handle_disconnected) 
        self.connect(con, SIGNAL('received(PyQt_PyObject,PyQt_PyObject)'), self.handle_received_ticket)

    def handle_disconnected(self, connection):
        self.connections.remove(connection)
        connection.close()
        del connection

    def handle_received_ticket(self, connection, object):
        if not isinstance(object, str):
            self.handle_disconnected(connection)

        ticket = object
        if ticket in self.connection_handlers:
            h = self.connection_handlers[ticket]
            self.disconnect(connection, SIGNAL('received(PyQt_PyObject,PyQt_PyObject)'), self.handle_received_ticket)
            h.set_connection(connection)
        else:
            self.handle_disconnected(connection)

    def load(rargs, self.cachedirname, options.pattern, options.format):
        if not self.loader:
            self.start_loader()

        self.loader.ship(('load', rargs, self.cachedirname, options.pattern, options.format ))

    def add_files(self, files):
        p = self.pile_viewer.get_pile()
        p.add_files(files)
        self.pile_viewer.update_contents()

    def update_progress(self, task, percent):
        self.pile_viewer.progressbars.set_status(task, percent)

    def snuffle(self,*args, **kwargs):
        win = SnufflerWindow(*args, **kwargs)
        return win

    def myCloseAllWindows(self, *args):
        self.closeAllWindows()
    
    def myQuit(self, *args):
        self.quit()

app = None

def snuffle(pile=None, **kwargs):
    '''View pile in a snuffler window.
    
    :param pile: :py:class:`pyrocko.pile.Pile` object to be visualized
    :param stations: list of `pyrocko.model.Station` objects or ``None``
    :param events: list of `pyrocko.model.Event` objects or ``None``
    :param markers: list of `pyrocko.gui_util.Marker` objects or ``None``
    :param ntracks: float, number of tracks to be shown initially (default: 12)
    :param follow: time interval (in seconds) for real time follow mode or ``None``
    :param controls: bool, whether to show the main controls (default: ``True``)
    :param opengl: bool, whether to use opengl (default: ``False``)
    '''
    
    if pile is None:
        pile = pyrocko.pile.make_pile()
    
    global app
    if app is None:
        app = Snuffler()

    win = app.snuffle(pile, **kwargs)
    app.exec_()
    ret = win.return_tag()
    
    del win
    gc.collect()

    return ret



