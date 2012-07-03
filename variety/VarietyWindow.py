# -*- Mode: Python; coding: utf-8; indent-tabs-mode: nil; tab-width: 4 -*-
### BEGIN LICENSE
# Copyright (C) 2012 Peter Levi <peterlevi@peterlevi.com>
# This program is free software: you can redistribute it and/or modify it 
# under the terms of the GNU General Public License version 3, as published 
# by the Free Software Foundation.
# 
# This program is distributed in the hope that it will be useful, but 
# WITHOUT ANY WARRANTY; without even the implied warranties of 
# MERCHANTABILITY, SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR 
# PURPOSE.  See the GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License along 
# with this program.  If not, see <http://www.gnu.org/licenses/>.
### END LICENSE

import gettext
from gettext import gettext as _

gettext.textdomain('variety')

from gi.repository import Gtk, Gdk, Gio # pylint: disable=E0611

from variety_lib import Window
from variety_lib import varietyconfig
from variety.AboutVarietyDialog import AboutVarietyDialog
from variety.PreferencesVarietyDialog import PreferencesVarietyDialog

import os
import shutil
import threading
import time

import logging

logger = logging.getLogger('variety')

import random

random.seed()

from variety.DominantColors import DominantColors
from variety.WallpapersNetDownloader import WallpapersNetDownloader
from variety.DesktopprDownloader import DesktopprDownloader
from variety.FlickrDownloader import FlickrDownloader
from variety.Options import Options

# See variety_lib.Window.py for more details about how this class works
class VarietyWindow(Window):
    __gtype_name__ = "VarietyWindow"

    SCHEMA = 'org.gnome.desktop.background'
    KEY = 'picture-uri'

    def finish_initializing(self, builder): # pylint: disable=E1002
        """Set up the main window"""
        super(VarietyWindow, self).finish_initializing(builder)

        self.gsettings = Gio.Settings.new(self.SCHEMA)

        self.AboutDialog = AboutVarietyDialog
        self.PreferencesDialog = PreferencesVarietyDialog

        self.prepare_config_folder()

        self.events = []

        self.wn_downloaders_cache = {}
        self.flickr_downloaders_cache = {}

        # load config
        self.reload_config()

        self.used = []
        self.used.append(self.gsettings.get_string(self.KEY).replace("file://", ""))
        self.position = 0
        self.current = self.used[self.position]

        self.last_change_time = 0

        self.image_count = -1
        self.image_cache = {}
        #TODO load image cache

        self.wheel_timer = None
        self.set_wp_timer = None

        self.update_indicator(self.current, False)

        self.start_threads()

        self.about = None
        self.preferences_dialog = None

    def prepare_config_folder(self):
        self.config_folder = os.path.expanduser("~/.config/variety")

        if not os.path.exists(os.path.join(self.config_folder, "variety.conf")):
            logger.info("Missing config file, copying it from " +
                        varietyconfig.get_data_file("config", "variety.conf"))
            shutil.copy(varietyconfig.get_data_file("config", "variety.conf"), self.config_folder)

    def reload_config(self):
        self.options = Options()
        self.options.read()

        try:
            os.makedirs(self.options.download_folder)
        except OSError:
            pass
        try:
            os.makedirs(self.options.favorites_folder)
        except OSError:
            pass

        self.individual_images = [os.path.expanduser(s[2]) for s in self.options.sources if
                                  s[0] and s[1] == Options.SourceType.IMAGE]

        self.folders = [os.path.expanduser(s[2]) for s in self.options.sources if s[0] and s[1] == Options.SourceType.FOLDER]

        if Options.SourceType.FAVORITES in [s[1] for s in self.options.sources if s[0]]:
            self.folders.append(self.options.favorites_folder)


        self.downloaders = []

        if Options.SourceType.DESKTOPPR in [s[1] for s in self.options.sources if s[0]]:
            self.downloaders.append(DesktopprDownloader(self.options.download_folder))

        self.wallpaper_net_urls = [s[2] for s in self.options.sources if s[0] and s[1] == Options.SourceType.WN]
        for url in self.wallpaper_net_urls:
            if url in self.wn_downloaders_cache:
                self.downloaders.append(self.wn_downloaders_cache[url])
            else:
                try:
                    dlr = WallpapersNetDownloader(url, self.options.download_folder)
                    self.wn_downloaders_cache[url] = dlr
                    self.downloaders.append(dlr)
                except Exception:
                    logger.exception("Could not create WallpapersNetDownloader for " + url)

        self.flickr_searches = [s[2] for s in self.options.sources if s[0] and s[1] == Options.SourceType.FLICKR]
        for search in self.flickr_searches:
            if search in self.flickr_downloaders_cache:
                self.downloaders.append(self.flickr_downloaders_cache[search])
            else:
                try:
                    dlr = FlickrDownloader(search, self.options.download_folder)
                    self.flickr_downloaders_cache[search] = dlr
                    self.downloaders.append(dlr)
                except Exception:
                    logger.exception("Could not create FlickrDownloader for " + search)

        for downloader in self.downloaders:
            try:
                os.makedirs(downloader.target_folder)
            except Exception:
                pass
            self.folders.append(downloader.target_folder)

        self.filters = [f[2] for f in self.options.filters if f[0]]

        logger.info("Loaded options:")
        logger.info("Change on start: " + str(self.options.change_on_start))
        logger.info("Change enabled: " + str(self.options.change_enabled))
        logger.info("Change interval: " + str(self.options.change_interval))
        logger.info("Download enabled: " + str(self.options.download_enabled))
        logger.info("Download interval: " + str(self.options.download_interval))
        logger.info("Download folder: " + self.options.download_folder)
        logger.info("Favorites folder: " + self.options.favorites_folder)
        logger.info("Color enabled: " + str(self.options.desired_color_enabled))
        logger.info("Color: " + (str(self.options.desired_color) if self.options.desired_color else "None"))
        logger.info("Images: " + str(self.individual_images))
        logger.info("Folders: " + str(self.folders))
        logger.info("WN URLs: " + str(self.wallpaper_net_urls))
        logger.info("Flickr searches: " + str(self.flickr_searches))
        logger.info("Total downloaders: " + str(len(self.downloaders)))
        logger.info("Filters: " + str(self.filters))

        # clean prepared - they are outdated
        self.prepared = []

        if self.events:
            for e in self.events:
                e.set()

    def start_threads(self):
        self.running = True

        self.prepared = []

        self.change_event = threading.Event()
        change_thread = threading.Thread(target=self.regular_change_thread)
        change_thread.daemon = True
        change_thread.start()

        self.prepare_event = threading.Event()
        prep_thread = threading.Thread(target=self.prepare_thread)
        prep_thread.daemon = True
        prep_thread.start()

        self.dl_event = threading.Event()
        dl_thread = threading.Thread(target=self.download_thread)
        dl_thread.daemon = True
        dl_thread.start()

        self.events = [self.change_event, self.prepare_event, self.dl_event]

    def update_indicator(self, file, is_gtk_thread):
        logger.info("Setting file info to: " + file)
        try:
            self.url = None
            label = os.path.dirname(file)
            if os.path.exists(file + ".txt"):
                with open(file + ".txt") as f:
                    lines = list(f)
                    if lines[0].strip() == "INFO:":
                        label = lines[1].strip()
                        self.url = lines[2].strip()

            if not is_gtk_thread:
                Gdk.threads_enter()

            for i in range(10):
                self.ind.prev.set_sensitive(self.position < len(self.used) - 1)
                self.ind.file_label.set_label(os.path.basename(file))
                self.ind.favorite.set_sensitive(
                    not os.path.normpath(file).startswith(os.path.normpath(self.options.favorites_folder)))

                self.ind.show_origin.set_label(label)
                self.ind.show_origin.set_sensitive(True)

                self.update_pause_resume()

            if not is_gtk_thread:
                Gdk.threads_leave()
        except Exception:
            logger.exception("Error updating file info")

    def update_pause_resume(self):
        self.ind.pause_resume.set_label("Pause" if self.options.change_enabled else "Resume")

    def regular_change_thread(self):
        logger.info("regular_change thread running")

        if self.options.change_on_start:
            self.change_event.wait(5) # wait for prepare thread to prepare some images first
            self.change_wallpaper()

        while self.running:
            self.change_event.wait(self.options.change_interval)
            self.change_event.clear()
            if not self.running:
                return
            if not self.options.change_enabled:
                continue
            while (time.time() - self.last_change_time) < self.options.change_interval:
                now = time.time()
                wait_more = self.options.change_interval - (now - self.last_change_time)
                self.change_event.wait(max(0, wait_more))
            if not self.options.change_enabled:
                continue
            logger.info("regular_change changes wallpaper")
            self.change_wallpaper()

    def prepare_thread(self):
        logger.info("prepare thread running")
        while self.running:
            try:
                logger.info("prepared buffer contains %s images" % len(self.prepared))

                if self.image_count < 0 or len(self.prepared) <= min(10, self.image_count // 20):
                    logger.info("preparing some images")
                    images = self.select_random_images(100)

                    found = 0
                    for fuzziness in xrange(2, 7):
                        if found > 10:
                            break
                        for img in images:
                            if self.image_ok(img, fuzziness):
                                self.prepared.append(img)
                                if self.options.desired_color_enabled:
                                    logger.debug("ok at fuzziness %s: %s" % (str(fuzziness), img))
                                found += 1

                    if not self.prepared and images:
                        logger.info("Prepared buffer still empty after search, appending some non-ok images")
                        self.prepared.append(images[0])

                    # remove duplicates
                    self.prepared = list(set(self.prepared))
                    random.shuffle(self.prepared)
                    if len(self.prepared) > 1 and self.prepared[0] == self.current:
                        self.prepared = self.prepared[1:]

                    logger.info("after search prepared buffer contains %s images" % len(self.prepared))
            except Exception:
                logger.exception("Error in prepare thread:")

            self.prepare_event.wait(30)
            self.prepare_event.clear()

    def download_thread(self):
        while self.running:
            try:
                self.dl_event.wait(self.options.download_interval)
                self.dl_event.clear()
                if not self.running:
                    return
                if not self.options.download_enabled:
                    continue
                if self.downloaders:
                    downloader = self.downloaders[random.randint(0, len(self.downloaders) - 1)]
                    downloader.download_one()
            except Exception:
                logger.exception("Could not download wallpaper:")

    def set_wp(self, filename):
        if self.set_wp_timer:
            self.set_wp_timer.cancel()
        self.set_wp_filename = filename
        self.set_wp_timer = threading.Timer(0.2, self.do_set_wp)
        self.set_wp_timer.start()

    def do_set_wp(self):
        self.set_wp_timer = None
        filename = self.set_wp_filename
        try:
            self.update_indicator(filename, False)
            to_set = filename
            if self.filters:
                filter = self.filters[random.randint(0, len(self.filters) - 1)]
                os.system(
                    "convert \"" + filename + "\" " + filter + " " + os.path.join(self.config_folder, "wallpaper.jpg"))
                to_set = os.path.join(self.config_folder, "wallpaper.jpg")
            self.gsettings.set_string(self.KEY, "file://" + to_set)
            self.gsettings.apply()
            self.current = filename
            self.last_change_time = time.time()
        except Exception:
            logger.exception("Error while setting wallpaper")

    def list_images(self):
        for filepath in self.individual_images:
            if self.is_image(filepath) and os.access(filepath, os.F_OK):
                yield filepath
        for folder in self.folders:
            if os.path.isdir(folder):
                try:
                    for root, subFolders, files in os.walk(folder):
                        for filename in files:
                            if self.is_image(filename):
                                yield os.path.join(root, filename)
                except Exception:
                    logger.exception("Cold not walk folder " + folder)

    def select_random_images(self, count):
        if self.image_count < 20 or random.randint(0, 20) == 0:
            cnt = sum(1 for f in self.list_images())
            if not cnt:
                return []

            self.image_count = cnt
        else:
            cnt = self.image_count

        indexes = set()
        for i in xrange(count):
            indexes.add(random.randint(0, cnt - 1))

        result = []
        for index, f in enumerate(self.list_images()):
            if index in indexes:
                result.append(f)
                indexes.remove(index)
                if not indexes:
                    break

        random.shuffle(result)
        return result

    def on_indicator_scroll(self, indicator, steps, direction, data=None):
        if self.wheel_timer:
            self.wheel_timer.cancel()
        self.wheel_direction = direction
        self.wheel_timer = threading.Timer(0.1, self.handle_scroll)
        self.wheel_timer.start()

    def handle_scroll(self):
        if self.wheel_direction:
            self.next_wallpaper()
        else:
            self.prev_wallpaper()
        self.timer = None

    def prev_wallpaper(self, widget=None, data=None):
        if self.position >= len(self.used) - 1:
            return
        else:
            self.position += 1
            self.set_wp(self.used[self.position])

    def next_wallpaper(self, widget=None, data=None):
        if self.position > 0:
            self.position -= 1
            self.set_wp(self.used[self.position])
        else:
            self.change_wallpaper()

    def change_wallpaper(self, widget=None, data=None):
        try:
            img  = None

            if len(self.prepared):
                try:
                    img = self.prepared.pop()
                    self.prepare_event.set()
                except Exception:
                    pass

            if not img:
                logger.info("No images yet in prepared buffer, using some random image")
                rnd_images = self.select_random_images(1)
                img = rnd_images[0] if rnd_images else None

            if not img:
                logger.info("No images found")
                return

            self.used = self.used[self.position:]
            self.used.insert(0, img)
            self.position = 0
            if len(self.used) > 1000:
                self.used = self.used[:1000]
            self.set_wp(img)
        except Exception:
            logger.exception("Could not change wallpaper")

    def image_ok(self, img, fuzziness):
        return img != self.current and self.color_ok(img, fuzziness)

    def color_ok(self, img, fuzziness):
        if not (self.options.desired_color_enabled and self.options.desired_color):
            return True
        try:
            if not img in self.image_cache:
                dom = DominantColors(img)
                self.image_cache[img] = dom.get_dominant()
            colors = self.image_cache[img]
            return DominantColors.contains_color(colors, self.options.desired_color, fuzziness)
        except Exception, err:
            logger.exception("Error in color_ok:")
            return False

    def open_folder(self, widget=None, data=None):
        os.system("xdg-open \"" + os.path.dirname(self.current) + "\"")

    def open_file(self, widget=None, data=None):
        os.system("xdg-open \"" + os.path.realpath(self.current) + "\"")

    def on_show_origin(self, widget=None, data=None):
        if self.url:
            os.system("xdg-open " + self.url)
        else:
            self.open_folder()

    def confirm_move(self, file, to):
        dialog = Gtk.MessageDialog(self, Gtk.DialogFlags.MODAL,
            Gtk.MessageType.QUESTION, Gtk.ButtonsType.YES_NO,
            "Move " + os.path.basename(file) + " to " + to + ". Are you sure?")
        dialog.set_title("Confirm Move to " + to)
        response = dialog.run()
        dialog.destroy()
        return response

    def move_file(self, file, to):
        try:
            shutil.move(file, to)
            try:
                shutil.move(file + ".txt", to)
            except Exception:
                pass
            logger.info("Moved " + file + " to " + to)
        except Exception:
            logger.exception("Could not move to " + to)

    def move_to_trash(self, widget=None, data=None):
        file = self.current
        if self.confirm_move(file, "Trash") == Gtk.ResponseType.YES:
            while self.used[self.position] == file:
                self.next_wallpaper()
            self.used = [f for f in self.used if f != file]
            trash = os.path.expanduser("~/.local/share/Trash/")
            self.move_file(file, trash)

    def move_to_favorites(self, widget=None, data=None):
        file = self.current
        if self.confirm_move(file, "Favorites") == Gtk.ResponseType.YES:
            new_file = os.path.join(self.options.favorites_folder, os.path.basename(file))
            self.used = [(new_file if f == file else f) for f in self.used]
            self.move_file(file, self.options.favorites_folder)
            if self.current == file:
                self.current = new_file
            self.update_indicator(self.current, True)

    def on_quit(self, widget=None):
        logger.info("Quitting")
        if self.running:
            if self.preferences_dialog:
                self.preferences_dialog.destroy()
            if self.about:
                self.about.destroy()
            self.running = False
            for e in self.events:
                e.set()
            Gtk.main_quit()
            os.unlink(os.path.expanduser("~/.config/variety/.lock"))

    def is_image(self, filename):
        return filename.lower().endswith(('.jpg', '.jpeg', '.gif', '.png'))

    def first_run(self):
        fr_file = os.path.join(self.config_folder, ".firstrun")
        if not os.path.exists(fr_file):
            f = open(fr_file, "w")
            f.write(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
            f.close()
            self.show()

    def on_continue_clicked(self, button=None):
        self.destroy()
        self.on_mnu_preferences_activate(button)

    def edit_prefs_file(self, widget=None):
        dialog = Gtk.MessageDialog(self, Gtk.DialogFlags.MODAL,
            Gtk.MessageType.INFO, Gtk.ButtonsType.OK,
            "I will open an editor with the config file and apply the changes after you save and close the editor.")
        dialog.set_title("Edit config file")
        response = dialog.run()
        dialog.destroy()
        os.system("gedit ~/.config/variety/variety.conf")
        self.reload_config()

    def on_pause_resume(self, widget=None):
        self.options.change_enabled = not self.options.change_enabled
        self.options.write()
        self.update_indicator(self.current, True)
