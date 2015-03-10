import datetime, asyncio

from PyQt4 import QtCore, QtGui, QtWebKit

import hangups
from hangups.ui.utils import get_conv_name

from qhangups.utils import text_to_segments, message_to_html
from qhangups.ui_qhangupsconversationwidget import Ui_QHangupsConversationWidget


class QHangupsConversationWidget(QtGui.QWidget, Ui_QHangupsConversationWidget):
    """Conversation tab"""
    def __init__(self, tab_parent, client, conv, parent=None):
        super().__init__(parent)
        self.setupUi(self)

        self.tab_parent = tab_parent
        self.client = client
        self.conv = conv

        self.client.on_disconnect.add_observer(self.on_disconnect)
        self.client.on_reconnect.add_observer(self.on_reconnect)
        self.conv.on_event.add_observer(self.on_event)
        self.conv.on_watermark_notification.add_observer(self.on_watermark_notification)

        self.messageTextEdit.textChanged.connect(self.on_text_changed)
        self.sendButton.clicked.connect(self.on_send_clicked)
        self.messagesWebView.page().mainFrame().contentsSizeChanged.connect(self.on_contents_size_changed)
        self.messagesWebView.page().linkClicked.connect(self.on_link_clicked)

        settings = QtCore.QSettings()
        self.enter_send_message = settings.value("enter_send_message", False, type=bool)

        # Install ourselves as event filter so we can catch Enter key press (see eventFilter method)
        self.messageTextEdit.installEventFilter(self)

        # Initialize QWebView with list of messages
        self.init_messages()

        self.num_unread_local = 0
        for event in self.conv.events:
            self.on_event(event, set_title=False, set_unread=False, check_duplicates=False)

    def eventFilter(self, obj, event):
        """Event filter for catching Enter key press and sending message"""
        if self.enter_send_message and obj is self.messageTextEdit:
            if event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_Return:
                self.on_send_clicked()
                return True
        return super().eventFilter(obj, event)

    def get_num_unread(self, local_unread=False):
        """Get number of unread messages (server-side or local)"""
        settings = QtCore.QSettings()
        if not settings.value("send_read_state", True, type=bool) or local_unread:
            num_unread = self.num_unread_local
        else:
            num_unread = len([conv_event for conv_event in self.conv.unread_events if
                              isinstance(conv_event, hangups.ChatMessageEvent) and
                              not self.conv.get_user(conv_event.user_id).is_self])
        return num_unread

    def set_title(self):
        """Update this conversation's tab title."""
        title = get_conv_name(self.conv, truncate=True)
        conv_widget_id = self.tab_parent.conversationsTabWidget.indexOf(self)
        num_unread = self.get_num_unread()
        if num_unread > 0:
            title += ' ({})'.format(num_unread)
            self.tab_parent.conversationsTabWidget.tabBar().setTabTextColor(conv_widget_id, QtCore.Qt.darkBlue)
        else:
            self.tab_parent.conversationsTabWidget.tabBar().setTabTextColor(conv_widget_id, QtGui.QColor())
        self.tab_parent.conversationsTabWidget.setTabText(conv_widget_id, title)
        self.tab_parent.conversationsTabWidget.setTabToolTip(conv_widget_id, title)

    def init_messages(self):
        """Initialize QWebView with list of messages"""
        self.messagesWebView.setContextMenuPolicy(QtCore.Qt.NoContextMenu)
        self.messagesWebView.page().setLinkDelegationPolicy(QtWebKit.QWebPage.DelegateAllLinks)
        #self.messagesWebView.settings().setAttribute(QtWebKit.QWebSettings.LocalContentCanAccessRemoteUrls, True)
        self.messagesWebView.setHtml(
            """<!doctype html>
            <html lang="en">
            <head>
                <meta charset="utf-8">
                <title>Messages</title>
                <style>
                    html { font-family: sans-serif; font-size: 10pt; }
                    div.message { margin-bottom: 1em; }
                </style>
            </head>
            <body>
                <div id="messages"></div>
            </body>
            </html>"""
        )

    def add_message(self, timestamp, text, username=None):
        """Add new message to list of messages"""
        datestr = "%d.%m. %H:%M" if timestamp.astimezone(tz=None).date() < datetime.date.today() else "%H:%M"
        message = "<b>{}{}:</b><br>\n{}<br>\n".format(timestamp.astimezone(tz=None).strftime(datestr),
                                                      " | {}".format(username) if username is not None else "",
                                                      text)
        doc = self.messagesWebView.page().mainFrame().documentElement()
        doc.findFirst("div[id=messages]").appendInside(
            """<div class="message">{}</div>""".format(message)
        )

    def is_current(self):
        """Is this conversation in current tab?"""
        return self.tab_parent.conversationsTabWidget.currentWidget() is self

    def set_active(self):
        """Activate conversation tab"""
        settings = QtCore.QSettings()

        # Set the client as active
        if settings.value("send_client_active", True, type=bool):
            future = asyncio.async(self.client.set_active())
            future.add_done_callback(lambda future: future.result())

        # Mark the newest event as read
        if settings.value("send_read_state", True, type=bool):
            future = asyncio.async(self.conv.update_read_timestamp())
            future.add_done_callback(lambda future: future.result())

        self.num_unread_local = 0
        self.set_title()
        self.messageTextEdit.setFocus()

    def on_contents_size_changed(self, size):
        """Size of contents in messagesWebView changed (callback)"""
        page = self.messagesWebView.page()
        viewport_height = page.viewportSize().height()
        scroll_position = page.mainFrame().scrollPosition().y()

        # Compute max. scroll position manually, because
        # scrollBarMaximum(QtCore.Qt.Vertical) doesn't work here
        scroll_max = page.mainFrame().contentsSize().height() - viewport_height

        # If user has scrolled more than half of viewport away, don't scroll down automatically
        if scroll_position < (scroll_max - 0.5 * viewport_height):
            return

        # Use singl-shot timer, because scrolling doesn't work here (maybe some Qt bug?)
        QtCore.QTimer.singleShot(0, self.on_contents_size_changed_timeout)

    def on_contents_size_changed_timeout(self):
        """Scroll to the bottom after timeout of single-shot timer (callback)"""
        frame = self.messagesWebView.page().mainFrame()
        frame.setScrollPosition(QtCore.QPoint(0, frame.scrollBarMaximum(QtCore.Qt.Vertical)))

    def on_link_clicked(self, url):
        """Open links in external web browser (callback)"""
        QtGui.QDesktopServices.openUrl(url)

    def on_text_changed(self):
        """Message text changed (callback)"""
        pass

    def on_send_clicked(self):
        """Send button pressed (callback)"""
        text = self.messageTextEdit.toPlainText()
        if not text.strip():
            return

        self.messageTextEdit.setEnabled(False)
        self.sendButton.setEnabled(False)

        segments = text_to_segments(text)
        asyncio.async(
            self.conv.send_message(segments)
        ).add_done_callback(self.on_message_sent)

    def on_message_sent(self, future):
        """Handle showing an error if a message fails to send (callback)"""
        try:
            future.result()
        except hangups.NetworkError:
            QtGui.QMessageBox.warning(self, self.tr("QHangups - Warning"),
                                      self.tr("Failed to send message!"))
        else:
            self.messageTextEdit.clear()
        finally:
            self.messageTextEdit.setEnabled(True)
            self.sendButton.setEnabled(True)
            self.messageTextEdit.setFocus()

    def on_disconnect(self):
        """Show that Hangups has disconnected from server (callback)"""
        self.add_message(datetime.datetime.now(tz=datetime.timezone.utc), "<i>*** disconnected ***</i>")

    def on_reconnect(self):
        """Show that Hangups has reconnected to server (callback)"""
        self.add_message(datetime.datetime.now(tz=datetime.timezone.utc), "<i>*** connected ***</i>")

    def on_watermark_notification(self, watermark_notification):
        """Update unread count after receiving watermark notification (callback)"""
        self.set_title()

    def on_event(self, conv_event, set_title=True, set_unread=True, check_duplicates=True):
        """Hangups event received (callback)"""
        # If event is already in conv._events_dict, don't do anything (fix for duplicate messages)
        if check_duplicates:
            try:
                self.conv.get_event(conv_event.id_)
                return
            except KeyError:
                pass

        user = self.conv.get_user(conv_event.user_id)

        if isinstance(conv_event, hangups.ChatMessageEvent):
            self.handle_message(conv_event, user, set_unread=set_unread)
        elif isinstance(conv_event, hangups.RenameEvent):
            self.handle_rename(conv_event, user)
        elif isinstance(conv_event, hangups.MembershipChangeEvent):
            self.handle_membership_change(conv_event, user)

        # Update the title in case unread count or conversation name changed.
        if set_title:
            self.set_title()

    def handle_message(self, conv_event, user, set_unread=True):
        """Handle received chat message"""
        self.add_message(conv_event.timestamp, message_to_html(conv_event), user.full_name)
        # Update the count of unread messages.
        if not user.is_self and set_unread and not self.is_current():
            self.num_unread_local += 1

    def handle_rename(self, conv_event, user):
        """Handle received rename event"""
        if conv_event.new_name == '':
            text = '<i>*** cleared the conversation name ***</i>'
        else:
            text = '<i>*** renamed the conversation to {} ***</i>'.format(conv_event.new_name)
        self.add_message(conv_event.timestamp, text, user.full_name)

    def handle_membership_change(self, conv_event, user):
        """Handle received membership change event"""
        event_users = [self.conv.get_user(user_id) for user_id in conv_event.participant_ids]
        names = ', '.join(user.full_name for user in event_users)
        if conv_event.type_ == hangups.MembershipChangeType.JOIN:
            self.add_message(conv_event.timestamp,
                             '<i>*** added {} to the conversation ***</i>'.format(names),
                             user.full_name)
        else:
            for name in names:
                self.add_message(conv_event.timestamp,
                                 '<i>*** left the conversation ***</i>',
                                 user.full_name)
