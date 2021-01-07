
import hashlib
import os
import sys
from threading import RLock
from typing import TYPE_CHECKING

from mcdreforged import constant
from mcdreforged.exception import IllegalCallError
from mcdreforged.plugin.metadata import MetaData
from mcdreforged.plugin.plugin import AbstractPlugin, PluginState
from mcdreforged.plugin.plugin_event import MCDRPluginEvents, EventListener
from mcdreforged.plugin.plugin_registry import DEFAULT_LISTENER_PRIORITY
from mcdreforged.utils import misc_util, string_util

if TYPE_CHECKING:
	from mcdreforged.plugin.plugin_manager import PluginManager

GLOBAL_LOAD_LOCK = RLock()


class RegularPlugin(AbstractPlugin):
	def __init__(self, plugin_manager: 'PluginManager', file_path: str):
		super().__init__(plugin_manager, file_path)
		self.file_name = os.path.basename(file_path)
		self.file_hash = None
		self.module_instance = None
		self.old_module_instance = None
		self.newly_loaded_module = []
		# noinspection PyTypeChecker
		self.__metadata = None  # type: MetaData

	def is_regular(self) -> bool:
		return True

	def get_metadata(self) -> MetaData:
		if self.__metadata is None:
			raise IllegalCallError('Meta data of plugin {} is not loaded. Plugin state = {}'.format(repr(self), self.state))
		return self.__metadata

	def get_fallback_metadata_id(self) -> str:
		return string_util.remove_suffix(self.file_name, constant.PLUGIN_FILE_SUFFIX)

	def __repr__(self):
		return 'RegularPlugin[file={},path={},state={}]'.format(self.file_name, self.file_path, self.state)

	def __register_default_listeners(self):
		for event in MCDRPluginEvents.get_event_list():
			if isinstance(event.default_method_name, str):
				func = getattr(self.module_instance, event.default_method_name, None)
				if callable(func):
					self.add_event_listener(event, EventListener(self, func, DEFAULT_LISTENER_PRIORITY))

	# --------------
	#   Life Cycle
	# --------------

	def __load_instance(self):
		self.file_hash = self.get_file_hash()
		with GLOBAL_LOAD_LOCK:
			previous_modules = sys.modules.copy()
			self.old_module_instance = self.module_instance
			try:
				self.module_instance = misc_util.load_source(self.file_path)
			finally:
				self.newly_loaded_module = [module for module in sys.modules if module not in previous_modules]
		self.__metadata = MetaData(self, getattr(self.module_instance, 'PLUGIN_METADATA', None))
		self.plugin_registry.clear()

	def load(self):
		self.assert_state({PluginState.UNINITIALIZED})
		self.__load_instance()
		self.mcdr_server.logger.debug('Plugin {} loaded from {}, file sha256 = {}'.format(self, self.file_path, self.file_hash))
		self.set_state(PluginState.LOADED)

	def ready(self):
		"""
		Get ready, and register default things (listeners etc.)
		"""
		self.assert_state({PluginState.LOADED, PluginState.READY})
		self.__register_default_listeners()
		self.set_state(PluginState.READY)

	def reload(self):
		self.assert_state({PluginState.LOADED, PluginState.READY})
		self.__load_instance()
		self.mcdr_server.logger.debug('RegularPlugin {} reloaded, file sha256 = {}'.format(self, self.file_hash))

	def unload(self):
		self.assert_state({PluginState.UNINITIALIZED, PluginState.LOADED, PluginState.READY})
		with GLOBAL_LOAD_LOCK:
			for module in self.newly_loaded_module:
				try:
					sys.modules.pop(module)
				except KeyError:
					self.mcdr_server.logger.critical('Module {} not found when unloading plugin {}'.format(module, repr(self)))
				else:
					self.mcdr_server.logger.debug('Removed module {} when unloading plugin {}'.format(module, repr(self)))
			self.newly_loaded_module.clear()
		self.set_state(PluginState.UNLOADING)

	def remove(self):
		self.assert_state({PluginState.UNLOADING})
		self.set_state(PluginState.UNLOADED)

	# ---------------
	#   Plugin File
	# ---------------

	def file_exists(self):
		return os.path.isfile(self.file_path)

	def file_changed(self):
		return self.get_file_hash() != self.file_hash

	def get_file_hash(self):
		if self.file_exists():
			with open(self.file_path, 'rb') as file:
				return hashlib.sha256(file.read()).hexdigest()
		else:
			return None
