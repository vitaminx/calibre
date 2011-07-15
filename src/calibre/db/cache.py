#!/usr/bin/env python
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__   = 'GPL v3'
__copyright__ = '2011, Kovid Goyal <kovid@kovidgoyal.net>'
__docformat__ = 'restructuredtext en'

import os
from collections import defaultdict
from functools import wraps

from calibre.db.locking import create_locks, RecordLock
from calibre.db.fields import create_field
from calibre.ebooks.book.base import Metadata
from calibre.utils.date import now

def api(f):
    f.is_cache_api = True
    return f

def read_api(f):
    f = api(f)
    f.is_read_api = True
    return f

def write_api(f):
    f = api(f)
    f.is_read_api = False
    return f

def wrap_simple(lock, func):
    @wraps(func)
    def ans(*args, **kwargs):
        with lock:
            return func(*args, **kwargs)
    return ans


class Cache(object):

    def __init__(self, backend):
        self.backend = backend
        self.fields = {}
        self.read_lock, self.write_lock = create_locks()
        self.record_lock = RecordLock(self.read_lock)
        self.format_metadata_cache = defaultdict(dict)

        # Implement locking for all simple read/write API methods
        # An unlocked version of the method is stored with the name starting
        # with a leading underscore. Use the unlocked versions when the lock
        # has already been acquired.
        for name in dir(self):
            func = getattr(self, name)
            ira = getattr(func, 'is_read_api', None)
            if ira is not None:
                # Save original function
                setattr(self, '_'+name, func)
                # Wrap it in a lock
                lock = self.read_lock if ira else self.write_lock
                setattr(self, name, wrap_simple(lock, func))

    def _format_abspath(self, book_id, fmt):
        '''
        Return absolute path to the ebook file of format `format`

        WARNING: This method will return a dummy path for a network backend DB,
        so do not rely on it, use format(..., as_path=True) instead.

        Currently used only in calibredb list, the viewer and the catalogs (via
        get_data_as_dict()).

        Apart from the viewer, I don't believe any of the others do any file
        I/O with the results of this call.
        '''
        try:
            name = self.fields['formats'].format_fname(book_id, fmt)
            path = self._field_for('path', book_id).replace('/', os.sep)
        except:
            return None
        if name and path:
            return self.backend.format_abspath(book_id, fmt, name, path)

    def _get_metadata(self, book_id, get_user_categories=True):
        mi = Metadata(None)
        author_ids = self._field_ids_for('authors', book_id)
        aut_list = [self._author_data(i) for i in author_ids]
        aum = []
        aus = {}
        aul = {}
        for rec in aut_list:
            aut = rec['name']
            aum.append(aut)
            aus[aut] = rec['sort']
            aul[aut] = rec['link']
        mi.title       = self._field_for('title', book_id,
                default_value=_('Unknown'))
        mi.authors     = aum
        mi.author_sort = self._field_for('author_sort', book_id,
                default_value=_('Unknown'))
        mi.author_sort_map = aus
        mi.author_link_map = aul
        mi.comments    = self._field_for('comments', book_id)
        mi.publisher   = self._field_for('publisher', book_id)
        n = now()
        mi.timestamp   = self._field_for('timestamp', book_id, default_value=n)
        mi.pubdate     = self._field_for('pubdate', book_id, default_value=n)
        mi.uuid        = self._field_for('uuid', book_id,
                default_value='dummy')
        mi.title_sort  = self._field_for('sort', book_id,
                default_value=_('Unknown'))
        mi.book_size   = self._field_for('size', book_id, default_value=0)
        mi.ondevice_col = self._field_for('ondevice', book_id, default_value='')
        mi.last_modified = self._field_for('last_modified', book_id,
                default_value=n)
        formats = self._field_for('formats', book_id)
        mi.format_metadata = {}
        if not formats:
            formats = None
        else:
            for f in formats:
                mi.format_metadata[f] = self._format_metadata(book_id, f)
            formats = ','.join(formats)
        mi.formats = formats
        mi.has_cover = _('Yes') if self._field_for('cover', book_id,
                default_value=False) else ''
        mi.tags = list(self._field_for('tags', book_id, default_value=()))
        mi.series = self._field_for('series', book_id)
        if mi.series:
            mi.series_index = self._field_for('series_index', book_id,
                    default_value=1.0)
        mi.rating = self._field_for('rating', book_id)
        mi.set_identifiers(self._field_for('identifiers', book_id,
            default_value={}))
        mi.application_id = book_id
        mi.id = book_id
        composites = {}
        for key, meta in self.field_metadata.custom_iteritems():
            mi.set_user_metadata(key, meta)
            if meta['datatype'] == 'composite':
                composites.append(key)
            else:
                mi.set(key, val=self._field_for(meta['label'], book_id),
                        extra=self._field_for(meta['label']+'_index', book_id))
        for c in composites:
            mi.set(key, val=self._composite_for(key, book_id, mi))

        user_cat_vals = {}
        if get_user_categories:
            user_cats = self.prefs['user_categories']
            for ucat in user_cats:
                res = []
                for name,cat,ign in user_cats[ucat]:
                    v = mi.get(cat, None)
                    if isinstance(v, list):
                        if name in v:
                            res.append([name,cat])
                    elif name == v:
                        res.append([name,cat])
                user_cat_vals[ucat] = res
        mi.user_categories = user_cat_vals

        return mi

    # Cache Layer API {{{

    @api
    def init(self):
        '''
        Initialize this cache with data from the backend.
        '''
        with self.write_lock:
            self.backend.read_tables()

            for field, table in self.backend.tables.iteritems():
                self.fields[field] = create_field(field, table)

            self.fields['ondevice'] = create_field('ondevice', None)

    @read_api
    def field_for(self, name, book_id, default_value=None):
        '''
        Return the value of the field ``name`` for the book identified by
        ``book_id``. If no such book exists or it has no defined value for the
        field ``name`` or no such field exists, then ``default_value`` is returned.

        The returned value for is_multiple fields are always tuples.
        '''
        try:
            return self.fields[name].for_book(book_id, default_value=default_value)
        except (KeyError, IndexError):
            return default_value

    @read_api
    def composite_for(self, name, book_id, mi, default_value=''):
        try:
            f = self.fields[name]
        except KeyError:
            return default_value

        f.render_composite(book_id, mi)

    @read_api
    def field_ids_for(self, name, book_id):
        '''
        Return the ids (as a tuple) for the values that the field ``name`` has on the book
        identified by ``book_id``. If there are no values, or no such book, or
        no such field, an empty tuple is returned.
        '''
        try:
            return self.fields[name].ids_for_book(book_id)
        except (KeyError, IndexError):
            return ()

    @read_api
    def books_for_field(self, name, item_id):
        '''
        Return all the books associated with the item identified by
        ``item_id``, where the item belongs to the field ``name``.

        Returned value is a tuple of book ids, or the empty tuple if the item
        or the field does not exist.
        '''
        try:
            return self.fields[name].books_for(item_id)
        except (KeyError, IndexError):
            return ()

    @read_api
    def all_book_ids(self):
        '''
        Frozen set of all known book ids.
        '''
        return frozenset(self.fields['uuid'].iter_book_ids())

    @read_api
    def all_field_ids(self, name):
        '''
        Frozen set of ids for all values in the field ``name``.
        '''
        return frozenset(iter(self.fields[name]))

    @read_api
    def author_data(self, author_id):
        '''
        Return author data as a dictionary with keys: name, sort, link

        If no author with the specified id is found an empty dictionary is
        returned.
        '''
        try:
            return self.fields['authors'].author_data(author_id)
        except (KeyError, IndexError):
            return {}

    @read_api
    def format_metadata(self, book_id, fmt, allow_cache=True):
        if not fmt:
            return {}
        fmt = fmt.upper()
        if allow_cache:
            x = self.format_metadata_cache[book_id].get(fmt, None)
            if x is not None:
                return x
        try:
            name = self.fields['formats'].format_fname(book_id, fmt)
            path = self._field_for('path', book_id).replace('/', os.sep)
        except:
            return {}

        ans = {}
        if path and name:
            ans = self.backend.format_metadata(book_id, fmt, name, path)
            self.format_metadata_cache[book_id][fmt] = ans
        return ans

    @api
    def get_metadata(self, book_id,
            get_cover=False, get_user_categories=True, cover_as_data=False):
        '''
        Return metadata for the book identified by book_id as a :class:`Metadata` object.
        Note that the list of formats is not verified. If get_cover is True,
        the cover is returned, either a path to temp file as mi.cover or if
        cover_as_data is True then as mi.cover_data.
        '''

        with self.read_lock:
            mi = self._get_metadata(book_id, get_user_categories=get_user_categories)

        if get_cover:
            if cover_as_data:
                cdata = self.cover(book_id)
                if cdata:
                    mi.cover_data = ('jpeg', cdata)
            else:
                mi.cover = self.cover(book_id, as_path=True)

        return mi

    @api
    def cover(self, book_id,
            as_file=False, as_image=False, as_path=False):
        '''
        Return the cover image or None. By default, returns the cover as a
        bytestring.

        WARNING: Using as_path will copy the cover to a temp file and return
        the path to the temp file. You should delete the temp file when you are
        done with it.

        :param as_file: If True return the image as an open file object (a SpooledTemporaryFile)
        :param as_image: If True return the image as a QImage object
        :param as_path: If True return the image as a path pointing to a
                        temporary file
        '''
        with self.read_lock:
            try:
                path = self._field_for('path', book_id).replace('/', os.sep)
            except:
                return None

        with self.record_lock.lock(book_id):
            return self.backend.cover(path, as_file=as_file, as_image=as_image,
                    as_path=as_path)

    # }}}

# Testing {{{

def test(library_path):
    from calibre.db.backend import DB
    backend = DB(library_path)
    cache = Cache(backend)
    cache.init()
    print ('All book ids:', cache.all_book_ids())

if __name__ == '__main__':
    from calibre.utils.config import prefs
    test(prefs['library_path'])

# }}}
