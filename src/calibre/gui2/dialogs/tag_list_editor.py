#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: GPLv3 Copyright: 2008, Kovid Goyal <kovid at kovidgoyal.net>


from PyQt5.Qt import (Qt, QDialog, QTableWidgetItem, QIcon, QByteArray, QSize,
                      QDialogButtonBox, QTableWidget, QItemDelegate, QApplication,
                      pyqtSignal, QAction, QFrame, QLabel, QTimer)

from calibre.gui2.dialogs.tag_list_editor_ui import Ui_TagListEditor
from calibre.gui2.complete2 import EditWithComplete
from calibre.gui2.dialogs.confirm_delete import confirm
from calibre.gui2.widgets import EnLineEdit
from calibre.gui2 import question_dialog, error_dialog, gprefs
from calibre.utils.config import prefs
from calibre.utils.icu import contains, primary_contains, primary_startswith
from polyglot.builtins import unicode_type

QT_HIDDEN_CLEAR_ACTION = '_q_qlineeditclearaction'


class NameTableWidgetItem(QTableWidgetItem):

    def __init__(self, sort_key):
        QTableWidgetItem.__init__(self)
        self.initial_value = ''
        self.current_value = ''
        self.is_deleted = False
        self.is_placeholder = False
        self.sort_key = sort_key

    def data(self, role):
        if role == Qt.DisplayRole:
            if self.is_deleted:
                return ''
            return self.current_value
        elif role == Qt.EditRole:
            return self.current_value
        else:
            return QTableWidgetItem.data(self, role)

    def set_is_deleted(self, to_what):
        if to_what:
            self.setIcon(QIcon(I('trash.png')))
        else:
            self.setIcon(QIcon(None))
            self.current_value = self.initial_value
        self.is_deleted = to_what

    def setData(self, role, data):
        if role == Qt.EditRole:
            self.current_value = data
        QTableWidgetItem.setData(self, role, data)

    def set_initial_text(self, txt):
        self.initial_value = txt

    def initial_text(self):
        return self.initial_value

    def text(self):
        return self.current_value

    def setText(self, txt):
        self.is_placeholder = False
        self.current_value = txt
        QTableWidgetItem.setText(self, txt)

    # Before this method is called, signals should be blocked for the
    # table containing this item
    def set_placeholder(self, txt):
        self.text_before_placeholder = self.current_value
        self.setText(txt)
        self.is_placeholder = True

    # Before this method is called, signals should be blocked for the
    # table containing this item
    def reset_placeholder(self):
        if self.is_placeholder:
            self.setText(self.text_before_placeholder)

    def __ge__(self, other):
        return (self.sort_key(unicode_type(self.text())) >=
                    self.sort_key(unicode_type(other.text())))

    def __lt__(self, other):
        return (self.sort_key(unicode_type(self.text())) <
                    self.sort_key(unicode_type(other.text())))


class CountTableWidgetItem(QTableWidgetItem):

    def __init__(self, count):
        QTableWidgetItem.__init__(self, unicode_type(count))
        self._count = count

    def __ge__(self, other):
        return self._count >= other._count

    def __lt__(self, other):
        return self._count < other._count


class EditColumnDelegate(QItemDelegate):
    editing_finished = pyqtSignal(int)
    editing_started  = pyqtSignal(int)

    def __init__(self, table):
        QItemDelegate.__init__(self)
        self.table = table
        self.completion_data = None

    def set_completion_data(self, data):
        self.completion_data = data

    def createEditor(self, parent, option, index):
        self.editing_started.emit(index.row())
        if index.column() == 0:
            self.item = self.table.itemFromIndex(index)
            if self.item.is_deleted:
                return None
            if self.completion_data:
                editor = EditWithComplete(parent)
                editor.set_separator(None)
                editor.update_items_cache(self.completion_data)
            else:
                editor = EnLineEdit(parent)
            return editor
        return None

    def destroyEditor(self, editor, index):
        self.editing_finished.emit(index.row())
        QItemDelegate.destroyEditor(self, editor, index)


class TagListEditor(QDialog, Ui_TagListEditor):

    def __init__(self, window, cat_name, tag_to_match, get_book_ids, sorter,
                 ttm_is_first_letter=False):
        QDialog.__init__(self, window)
        Ui_TagListEditor.__init__(self)
        self.setupUi(self)
        self.verticalLayout_2.setAlignment(Qt.AlignCenter)
        self.search_box.setMinimumContentsLength(25)

        # Put the category name into the title bar
        t = self.windowTitle()
        self.category_name = cat_name
        self.setWindowTitle(t + ' (' + cat_name + ')')
        # Remove help icon on title bar
        icon = self.windowIcon()
        self.setWindowFlags(self.windowFlags()&(~Qt.WindowContextHelpButtonHint))
        self.setWindowIcon(icon)

        # Get saved geometry info
        try:
            self.table_column_widths = \
                        gprefs.get('tag_list_editor_table_widths', None)
        except:
            pass

        # initialization
        self.to_rename = {}
        self.to_delete = set()
        self.all_tags = {}
        self.original_names = {}

        self.ordered_tags = []
        self.sorter = sorter
        self.get_book_ids = get_book_ids
        self.text_before_editing = ''

        # Capture clicks on the horizontal header to sort the table columns
        hh = self.table.horizontalHeader()
        hh.sectionResized.connect(self.table_column_resized)
        hh.setSectionsClickable(True)
        hh.sectionClicked.connect(self.do_sort)
        hh.setSortIndicatorShown(True)

        self.last_sorted_by = 'name'
        self.name_order = 0
        self.count_order = 1
        self.was_order = 1

        self.edit_delegate = EditColumnDelegate(self.table)
        self.edit_delegate.editing_finished.connect(self.stop_editing)
        self.edit_delegate.editing_started.connect(self.start_editing)
        self.table.setItemDelegateForColumn(0, self.edit_delegate)

        if prefs['use_primary_find_in_search']:
            self.string_contains = primary_contains
        else:
            self.string_contains = contains

        self.delete_button.clicked.connect(self.delete_tags)
        self.table.delete_pressed.connect(self.delete_pressed)
        self.rename_button.clicked.connect(self.rename_tag)
        self.undo_button.clicked.connect(self.undo_edit)
        self.table.itemDoubleClicked.connect(self._rename_tag)
        self.table.itemChanged.connect(self.finish_editing)

        self.buttonBox.button(QDialogButtonBox.Ok).setText(_('&OK'))
        self.buttonBox.button(QDialogButtonBox.Cancel).setText(_('&Cancel'))
        self.buttonBox.accepted.connect(self.accepted)

        self.search_box.initialize('tag_list_search_box_' + cat_name)
        le = self.search_box.lineEdit()
        ac = le.findChild(QAction, QT_HIDDEN_CLEAR_ACTION)
        if ac is not None:
            ac.triggered.connect(self.clear_search)
        self.search_box.textChanged.connect(self.search_text_changed)
        self.search_button.clicked.connect(self.do_search)
        self.search_button.setDefault(True)
        l = QLabel(self.table)
        self.not_found_label = l
        l.setFrameStyle(QFrame.StyledPanel)
        l.setAutoFillBackground(True)
        l.setText(_('No matches found'))
        l.setAlignment(Qt.AlignVCenter)
        l.resize(l.sizeHint())
        l.move(10, 0)
        l.setVisible(False)
        self.not_found_label_timer = QTimer()
        self.not_found_label_timer.setSingleShot(True)
        self.not_found_label_timer.timeout.connect(
                self.not_found_label_timer_event, type=Qt.QueuedConnection)

        self.filter_box.initialize('tag_list_filter_box_' + cat_name)
        le = self.filter_box.lineEdit()
        ac = le.findChild(QAction, QT_HIDDEN_CLEAR_ACTION)
        if ac is not None:
            ac.triggered.connect(self.clear_filter)
        le.returnPressed.connect(self.do_filter)
        self.filter_button.clicked.connect(self.do_filter)

        self.apply_vl_checkbox.clicked.connect(self.vl_box_changed)

        self.table.setEditTriggers(QTableWidget.EditKeyPressed)

        try:
            geom = gprefs.get('tag_list_editor_dialog_geometry', None)
            if geom is not None:
                QApplication.instance().safe_restore_geometry(self, QByteArray(geom))
            else:
                self.resize(self.sizeHint()+QSize(150, 100))
        except:
            pass
        # Add the data
        self.search_item_row = -1
        self.fill_in_table(None, tag_to_match, ttm_is_first_letter)

    def vl_box_changed(self):
        self.search_item_row = -1
        self.fill_in_table(None, None, False)

    def do_search(self):
        self.not_found_label.setVisible(False)
        find_text = icu_lower(unicode_type(self.search_box.currentText()))
        if not find_text:
            return
        for _ in range(0, self.table.rowCount()):
            r = self.search_item_row = (self.search_item_row + 1) % self.table.rowCount()
            if self.string_contains(find_text,
                        self.table.item(r, 0).text()):
                self.table.setCurrentItem(self.table.item(r, 0))
                self.table.setFocus(True)
                return
        # Nothing found. Pop up the little dialog for 1.5 seconds
        self.not_found_label.setVisible(True)
        self.not_found_label_timer.start(1500)

    def search_text_changed(self):
        self.search_item_row = -1

    def clear_search(self):
        self.search_item_row = -1
        self.search_box.setText('')

    def fill_in_table(self, tags, tag_to_match, ttm_is_first_letter):
        data = self.get_book_ids(self.apply_vl_checkbox.isChecked())
        self.all_tags = {}
        filter_text = icu_lower(unicode_type(self.filter_box.text()))
        for k,v,count in data:
            if not filter_text or self.string_contains(filter_text, icu_lower(v)):
                self.all_tags[v] = {'key': k, 'count': count, 'cur_name': v,
                                   'is_deleted': k in self.to_delete}
                self.original_names[k] = v
        self.edit_delegate.set_completion_data(self.original_names.values())

        self.ordered_tags = sorted(self.all_tags.keys(), key=self.sorter)
        if tags is None:
            tags = self.ordered_tags

        select_item = None
        self.table.blockSignals(True)
        self.table.clear()
        self.table.setColumnCount(3)
        self.name_col = QTableWidgetItem(self.category_name)
        self.table.setHorizontalHeaderItem(0, self.name_col)
        self.count_col = QTableWidgetItem(_('Count'))
        self.table.setHorizontalHeaderItem(1, self.count_col)
        self.was_col = QTableWidgetItem(_('Was'))
        self.table.setHorizontalHeaderItem(2, self.was_col)

        self.table.setRowCount(len(tags))
        for row,tag in enumerate(tags):
            item = NameTableWidgetItem(self.sorter)
            item.set_is_deleted(self.all_tags[tag]['is_deleted'])
            _id = self.all_tags[tag]['key']
            item.setData(Qt.UserRole, _id)
            item.set_initial_text(tag)
            if _id in self.to_rename:
                item.setText(self.to_rename[_id])
            else:
                item.setText(tag)
            item.setFlags(item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)
            if select_item is None:
                if ttm_is_first_letter:
                    if primary_startswith(tag, tag_to_match):
                        select_item = item
                elif tag == tag_to_match:
                    select_item = item
            item = CountTableWidgetItem(self.all_tags[tag]['count'])
            # only the name column can be selected
            item.setFlags(item.flags() & ~(Qt.ItemIsSelectable|Qt.ItemIsEditable))
            self.table.setItem(row, 1, item)

            item = QTableWidgetItem()
            item.setFlags(item.flags() & ~(Qt.ItemIsSelectable|Qt.ItemIsEditable))
            if _id in self.to_rename or _id in self.to_delete:
                item.setData(Qt.DisplayRole, tag)
            self.table.setItem(row, 2, item)

        if self.last_sorted_by == 'name':
            self.table.sortByColumn(0, self.name_order)
        elif self.last_sorted_by == 'count':
            self.table.sortByColumn(1, self.count_order)
        else:
            self.table.sortByColumn(2, self.was_order)

        if select_item is not None:
            self.table.setCurrentItem(select_item)
            self.table.setFocus(True)
            self.start_find_pos = select_item.row()
        else:
            self.table.setCurrentCell(0, 0)
            self.search_box.setFocus()
            self.start_find_pos = -1
        self.table.blockSignals(False)

    def not_found_label_timer_event(self):
        self.not_found_label.setVisible(False)

    def clear_filter(self):
        self.filter_box.setText('')
        self.fill_in_table(None, None, False)

    def do_filter(self):
        self.fill_in_table(None, None, False)

    def table_column_resized(self, col, old, new):
        self.table_column_widths = []
        for c in range(0, self.table.columnCount()):
            self.table_column_widths.append(self.table.columnWidth(c))

    def resizeEvent(self, *args):
        QDialog.resizeEvent(self, *args)
        if self.table_column_widths is not None:
            for c,w in enumerate(self.table_column_widths):
                self.table.setColumnWidth(c, w)
        else:
            # the vertical scroll bar might not be rendered, so might not yet
            # have a width. Assume 25. Not a problem because user-changed column
            # widths will be remembered
            w = self.table.width() - 25 - self.table.verticalHeader().width()
            w //= self.table.columnCount()
            for c in range(0, self.table.columnCount()):
                self.table.setColumnWidth(c, w)

    def save_geometry(self):
        gprefs['tag_list_editor_table_widths'] = self.table_column_widths
        gprefs['tag_list_editor_dialog_geometry'] = bytearray(self.saveGeometry())

    def start_editing(self, on_row):
        items = self.table.selectedItems()
        self.table.blockSignals(True)
        for item in items:
            if item.row() != on_row:
                item.set_placeholder(_('Editing...'))
            else:
                self.text_before_editing = item.text()
        self.table.blockSignals(False)

    def stop_editing(self, on_row):
        items = self.table.selectedItems()
        self.table.blockSignals(True)
        for item in items:
            if item.row() != on_row and item.is_placeholder:
                item.reset_placeholder()
        self.table.blockSignals(False)

    def finish_editing(self, edited_item):
        if not edited_item.text():
            error_dialog(self, _('Item is blank'), _(
                'An item cannot be set to nothing. Delete it instead.'), show=True)
            self.table.blockSignals(True)
            edited_item.setText(self.text_before_editing)
            self.table.blockSignals(False)
            return
        items = self.table.selectedItems()
        self.table.blockSignals(True)
        for item in items:
            id_ = int(item.data(Qt.UserRole))
            self.to_rename[id_] = unicode_type(edited_item.text())
            orig = self.table.item(item.row(), 2)
            item.setText(edited_item.text())
            orig.setData(Qt.DisplayRole, item.initial_text())
        self.table.blockSignals(False)

    def undo_edit(self):
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            error_dialog(self, _('No item selected'),
                         _('You must select one item from the list of Available items.')).exec_()
            return

        if not confirm(
            _('Do you really want to undo your changes?'),
            'tag_list_editor_undo'):
            return
        self.table.blockSignals(True)
        for idx in indexes:
            row = idx.row()
            item = self.table.item(row, 0)
            item.setText(item.initial_text())
            item.set_is_deleted(False)
            self.to_delete.discard(int(item.data(Qt.UserRole)))
            self.to_rename.pop(int(item.data(Qt.UserRole)), None)
            self.table.item(row, 2).setData(Qt.DisplayRole, '')
        self.table.blockSignals(False)

    def rename_tag(self):
        item = self.table.item(self.table.currentRow(), 0)
        self._rename_tag(item)

    def _rename_tag(self, item):
        if item is None:
            error_dialog(self, _('No item selected'),
                         _('You must select one item from the list of Available items.')).exec_()
            return
        col_zero_item = self.table.item(item.row(), 0)
        if col_zero_item.is_deleted:
            if not question_dialog(self, _('Undelete item?'),
                   '<p>'+_('That item is deleted. Do you want to undelete it?')+'<br>'):
                return
            col_zero_item.set_is_deleted(False)
            self.to_delete.discard(int(col_zero_item.data(Qt.UserRole)))
            orig = self.table.item(col_zero_item.row(), 2)
            self.table.blockSignals(True)
            orig.setData(Qt.DisplayRole, '')
            self.table.blockSignals(False)
        else:
            self.table.editItem(item)

    def delete_pressed(self):
        if self.table.currentColumn() == 0:
            self.delete_tags()

    def delete_tags(self):
        deletes = self.table.selectedItems()
        if not deletes:
            error_dialog(self, _('No items selected'),
                         _('You must select at least one item from the list.')).exec_()
            return

        to_del = []
        for item in deletes:
            if not item.is_deleted:
                to_del.append(item)

        if to_del:
            ct = ', '.join([unicode_type(item.text()) for item in to_del])
            if not confirm(
                '<p>'+_('Are you sure you want to delete the following items?')+'<br>'+ct,
                'tag_list_editor_delete'):
                return

        row = self.table.row(deletes[0])
        self.table.blockSignals(True)
        for item in deletes:
            id_ = int(item.data(Qt.UserRole))
            self.to_delete.add(id_)
            item.set_is_deleted(True)
            orig = self.table.item(item.row(), 2)
            orig.setData(Qt.DisplayRole, item.initial_text())
        self.table.blockSignals(False)
        if row >= self.table.rowCount():
            row = self.table.rowCount() - 1
        if row >= 0:
            self.table.scrollToItem(self.table.item(row, 0))

    def do_sort(self, section):
        (self.do_sort_by_name, self.do_sort_by_count, self.do_sort_by_was)[section]()

    def do_sort_by_name(self):
        self.name_order = 1 - self.name_order
        self.last_sorted_by = 'name'
        self.table.sortByColumn(0, self.name_order)

    def do_sort_by_count(self):
        self.count_order = 1 - self.count_order
        self.last_sorted_by = 'count'
        self.table.sortByColumn(1, self.count_order)

    def do_sort_by_was(self):
        self.was_order = 1 - self.was_order
        self.last_sorted_by = 'count'
        self.table.sortByColumn(2, self.was_order)

    def accepted(self):
        self.save_geometry()
