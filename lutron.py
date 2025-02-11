#!/usr/bin/env python3

'''
Asyncio library for communicating with Lutron caseta devices via Bridge pro, using LEAP protocol
MQTT interface
Based on https://github.com/gurumitts/pylutron-caseta
LEAP CommandType:
    GoToDimmedLevel
    GoToFanSpeed
    GoToLevel
    PressAndHold
    PressAndRelease
    Release
    ShadeLimitLower
    ShadeLimitRaise
    Raise
    Lower
    Stop
24/5/2022 V 1.0.0 N Waterton - Initial Release
'''

import logging
from logging.handlers import RotatingFileHandler
import sys, argparse, os
from datetime import timedelta
from inspect import signature
import asyncio
import time

from pylutron_caseta.smartbridge import Smartbridge, _LEAP_DEVICE_TYPES

from mqtt import MQTT

__version__ = __VERSION__ = '1.0.0'

class Device():
    '''
    Generic Device Class
    '''

    def __init__(self, device, parent=None):
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.device = device
        self.parent = parent
        self.loop = asyncio.get_event_loop()
        
    def __call__(self):
        self.log.info('{}: {}, ID: {} value: {}'.format(self.type, self.name, self.device_id, self.current_state))
        self.publish(self.name, self.current_state)
        
    def __bool__(self):
        if self.parent:
            return self.parent.bridge.is_on(self.device_id)
        return self.current_state > 0
        
    def __str__(self):
        return 'ON' if bool(self) else 'OFF'
            
    @property
    def name(self):
        return self.device['name'].replace(" ","_").lower()
        
    @property
    def device_id(self):
        return self.device['device_id']
        
    @property
    def type(self):
        return self.device['type']
        
    @property
    def model(self):
        return self.device['model']
        
    @property
    def serial(self):
        return self.device['serial']
        
    @property
    def zone(self):
        return self.device['zone']
        
    @property
    def occupancy_sensors(self):
        return self.device['occupancy_sensors']
        
    @property
    def current_state(self):
        return self.device.get('current_state')
        
    def publish(self, topic, msg):
        if self.parent:
            self.parent._publish(topic, msg)

class LightDimmer(Device):
    '''
    Dimmer callback class
    '''

    def __init__(self, device, parent=None):
        super().__init__(device, parent)
        self.log = logging.getLogger('Main.'+__class__.__name__)

class LightSwitch(Device):
    '''
    Switch callback class
    '''

    def __init__(self, device, parent=None):
        super().__init__(device, parent)
        self.log = logging.getLogger('Main.'+__class__.__name__)
        
class Fan(Device):
    '''
    Switch callback class
    '''

    def __init__(self, device, parent=None):
        super().__init__(device, parent)
        self.log = logging.getLogger('Main.'+__class__.__name__)
        
    @property
    def fan_speed(self):
        return self.device['fan_speed']
        
class Blind(Device):
    '''
    Switch callback class
    '''

    def __init__(self, device, parent=None):
        super().__init__(device, parent)
        self.log = logging.getLogger('Main.'+__class__.__name__)
        
    @property
    def tilt(self):
        return self.device['tilt']

class PicoButton(Device):
    '''
    Pico Devices callback and utility class
    '''
    
    picobuttons = {"Pico1Button":           {0:"Button"},
                   "Pico2Button":           {0:"On", 1:"Off"},
                   "Pico2ButtonRaiseLower": {0:"On", 1:"Off", 2:"Raise", 3:"Lower"},
                   "Pico3Button":           {0:"On", 1:"Fav", 2:"Off"},
                   "Pico3ButtonRaiseLower": {0:"On", 1:"Fav", 2:"Off", 3:"Raise", 4:"Lower"},
                   "Pico4Button":           {0:"1", 1:"2", 2:"3", 3:"4"},
                   "Pico4ButtonScene":      {0:"On", 1:"Off", 2:"Preset 1", 3:"Preset 2"},
                   "Pico4Button2Group":     {0:"Group 1 On", 1:"Group 1 Off 2", 2:"Group 2 On", 3:"Group 2 Off"},
                   "FourGroupRemote":       {0:"Group 1 On", 1:"Group 2 On 2", 2:"Group 3 On", 3:"Group 4 On"}
                  }
    
    def __init__(self, device, parent=None):
        super().__init__(device, parent)
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.double_click_time = 0.5    #not long enough to capture Raise and Lower double click
        self.long_press_time = 1
        self.start = self.loop.time()
        self._long_press_task = None
        if self.type not in self.picobuttons.keys():
            self.log.warning('Adding button type: {}'.format(self.type))
            self.picobuttons[self.type] = {}
            
    def __call__(self, msg=None):
        if msg is None:
            msg = self.current_state
        elif self.current_state != msg:
            self.current_state = msg
        self.log.info('{}: {}, Button: {}({}), action: {}'.format(self.type, self.name, self.button_number, self.button_name, str(self)))
        self.publish('{}/{}'.format(self.name, self.button_number), str(self))
        self.timing()
        
    def __bool__(self):
        return self.current_state == 'Press'
            
    @property
    def button_groups(self):
        return self.device['button_groups']
        
    @property
    def button_number(self):
        return self.device['button_number']
         
    @property
    def button_name(self):
        return self.picobuttons[self.type].get(self.button_number, str(self.button_number))
        
    @property
    def current_state(self):
        return super().current_state
        
    @current_state.setter    
    def current_state(self, state):
        self.device['current_state'] = state
        
    def button_number_from_name(self, button_name):
        '''
        get button number from button name
        '''
        if button_name is None:
            return False
        if isinstance(button_name, int):
            return button_name
        if button_name.isdigit():
            return int(button_name) 
        return self.button_number if self.button_name.upper() == button_name.upper() else None
        
    def match(self, button_number):
        '''
        return True if button_number (name or number) matches this button
        '''
        return self.button_number == self.button_number_from_name(button_number)
            
    def timing(self):
        '''
        generate double click and long press events
        '''
        if bool(self):  #Press
            if self.loop.time() - self.start <= self.double_click_time:
                self.publish('{}/{}/double'.format(self.name, self.button_number), str(self))
            self.start = self.loop.time()
        self._long_press_task = self.long_press()
            
    def long_press(self):
        '''
        longpress timing
        returns asyncio.TimerHandle() or None
        
        if button is pressed, start callback in self.long_press_time seconds, return timer
        if button is released, and timer has not been cancelled, cancel timer. return None
        if time expires, publish button setting, cancel timer (canceled timer still exists)
        if button is released after timer expires (and has been cancelled, but is not None),
           publish current button setting, cancel timer and return None
        '''
        if bool(self):
            if not self._long_press_task:
                return self.loop.call_later(self.long_press_time, self.long_press)
        else:
            if self._long_press_task is None:
                return None
            if not self._long_press_task.cancelled():
                return self._long_press_task.cancel()   #returns None
        self.publish('{}/{}/long'.format(self.name, self.button_number), str(self))
        return self._long_press_task.cancel()           #returns None
        

class Caseta(MQTT):
    '''
    Represents a Lutron Caseta lighting System, with methods for status and issuing commands
    all methods not starting with '_' can be sent as commands to MQTT topic
    `Smartbridge` provides an API for interacting with the Caséta bridge using LEAP Protocol
    '''
    __version__ = __version__
    
    certs = {"keyfile":"caseta.key", "certfile":"caseta.crt", "ca_certs":"caseta-bridge.crt"}

    def __init__(self, bridgeip=None, log=None, **kwargs):
        super().__init__(log=log, **kwargs)
        self.log = log if log is not None else logging.getLogger('Main.'+__class__.__name__)
        self.log.info(f'{__class__.__name__} library v{__class__.__version__}')
        self.bridgeip = bridgeip
        self.bridge = None
        self.loop = asyncio.get_event_loop()
        self.bridge_methods = {func:getattr(Smartbridge, func) for func in dir(Smartbridge) if callable(getattr(Smartbridge, func)) and (not func.startswith("_") and not func in self._method_dict.keys()) }
        self._method_dict.update(self.bridge_methods)
        
    def _setup(self):
        if all([os.path.exists(f) for f in self.certs.values()]):
            self.bridge = Smartbridge.create_tls(self.bridgeip, **self.certs)
            return True
        return False
        
    async def _pair(self):
        from pylutron_caseta.pairing import async_pair
        def _ready():
            self.log.info("Press the small black button on the back of the bridge.")
        try:
            data = await async_pair(self.bridgeip, _ready)
            with open(self.certs["ca_certs"], "w") as cacert:
                cacert.write(data["ca"])
            with open(self.certs["certfile"], "w") as cert:
                cert.write(data["cert"])
            with open(self.certs["keyfile"], "w") as key:
                key.write(data["key"])
            self.log.info(f"Successfully paired with {data['version']}")
            return True
        except Exception as e:
            self.log.exception('Error pairing: {}'.format(e))
        return False
        
    async def _connect(self):
        while not self._setup():
            while not await self._pair():
                self.log.info('Retry pairing...')
                await asyncio.sleep(1)
        
        try:
            await self.bridge.connect()
            self.log.info("Connected to bridge: {}".format(self.bridgeip))
            self._publish('status', 'Connected')
                
            for id, scene in self.bridge.get_scenes().items():
                self.log.info('Found Scene: {} , {}'.format(id, scene)) 
            for device, setting in self.bridge.get_devices().items():
                self.log.debug("Found Device: {} : settings: {}".format(device, setting))
            for type in _LEAP_DEVICE_TYPES.keys():
                self._subscribe(type)

        except Exception as e:
            self.log.exception(e)
            
    def _subscribe(self, type):
        if type == 'sensor':
            for device in self.bridge.get_buttons().values():
                self.log.info("Found {}: {}".format(type, device))
                callback = PicoButton(device, self)
                self.bridge.add_button_subscriber(callback.device_id, callback)
                callback()     #publish current value
            return
        for device in self.bridge.get_devices_by_domain(type):
            self.log.info("Found {}: {}".format(type, device))
            if type == 'light':
                callback = LightDimmer(device, self)
            elif type == 'switch':
                callback = LightSwitch(device, self)
            elif type == 'fan':
                callback = Fan(device, self)
            elif type == 'cover':
                callback = Blind(device, self)
            else:
                callback = Device(device, self)
            self.bridge.add_subscriber(callback.device_id, callback)
            callback()     #publish current value
            
    def _device_id_from_name(self, device_name, button_number=None):
        if device_name:
            for device_id, device in self.bridge._button_subscribers.items():
                if device_name == device.name and device.match(button_number):
                    self.log.info("Found Button: {} : settings: {}".format(device_id, device.device))
                    return device_id, True
            for device_id, device in self.bridge._subscribers.items():
                if device_name == device.name:
                    self.log.info("Found Device: {} : settings: {}".format(device_id, device.device))
                    return device_id, False

            self.log.warning('Device: {} NOT FOUND'.format(device_name))
        return None, False
        
    def _device_name(self, device_id):
        name = self.bridge.get_devices().get(device_id, {}).get('name')
        if not name:
            name = self.bridge.get_buttons().get(device_id, {}).get('name')
        return name
            
    async def set_value(self, device_id, value, fade_time=0):
        '''
        Override set_value in Smartbridge to parse args
        '''
        if isinstance(value, tuple):
            fade_time = int(value[1])
            value = value[0]
        if isinstance(value, str):
            value = 0 if value.upper() == 'OFF' else 100 if value.upper() == 'ON' else int(value)
        self.log.debug('Setting: {}, to: {}%, fade time: {} s'.format(self._device_name(device_id), value, fade_time))
        await self.bridge.set_value(device_id, value, timedelta(seconds=fade_time)) 
        
    async def _button_action(self, button_id, action):
        '''
        Will perform action on the button of a pico device with the given button_id.
        :param button_id: device id of specific button
        :param action one of "PressAndRelease", "PressAndHold", "Release"
        '''
        await self.bridge._request(
            "CreateRequest",
            f"/button/{button_id}/commandprocessor",
            {"Command": {"CommandType": action}},
        )
        
    async def click(self, button_id):
        return await self._button_action(button_id, "PressAndRelease")
        
    async def press(self, button_id):
        return await self._button_action(button_id, "PressAndHold")
        
    async def release(self, button_id):
        return await self._button_action(button_id, "Release")
        
    async def refresh(self, refresh):
        if refresh in[1, '1', 'True', 'true', True]:
            return await self.bridge._login()
            
    async def status(self):
        return 'Connected' if self.bridge.is_connected() else 'Disconnected'
        
    def _get_command(self, msg):
        '''
        Override MQTT method
        extract command and args from MQTT msg, get device_id from device_name
        insert self.bridge if it's a bridge command
        '''
        command, args = super()._get_command(msg)
        device_name = msg.topic.split('/')[-2]
        device_name = msg.topic.split('/')[-1] if device_name == self._name else device_name
        device_name = None if device_name == command else device_name
        self.log.debug('Received command: {}, device: {}, args: {}'.format(command, device_name, args))
        args = [args] if not isinstance(args, list) else args
        nparams = len(signature(self._method_dict[command]).parameters) if command else len(args)
        br = self.bridge if command in self.bridge_methods.keys() else None
        device_id, is_button = self._device_id_from_name(device_name, *args)
        args = [str(a) if command == 'activate_scene' else a for a in args if a is not None]   #make scene_id string
        if device_id:
            if is_button:
                args = [device_id]
            else:
                args.insert(0, device_id)
        if br:
            args.insert(0, br)
        args = args[:nparams]  #truncate extra parameters
        self.log.debug('Sending command: command: {}, args: {}'.format(command, args))
        return command, args
        
    def stop(self):
        try:
            self.loop.run_until_complete(self._stop())
        except RuntimeError:
            self.loop.create_task(self._stop())
        
    async def _stop(self):
        '''
        put shutdown routines here
        '''
        await super()._stop()
        if self.bridge is not None:
            await self.bridge.close()
        
    def _publish(self, topic=None, message=None):
        if message is not None:
            super()._publish(topic, message)
        
        
def parse_args():
    
    #-------- Command Line -----------------
    parser = argparse.ArgumentParser(
        description='Forward MQTT data to Lutron API')
    parser.add_argument(
        'bridgeip',
        action='store',
        type=str,
        default=None,
        help='Bridge ip Address (default: %(default)s)')
    parser.add_argument(
        '-t', '--topic',
        action='store',
        type=str,
        default="/lutron/command",
        help='MQTT Topic to send commands to, (can use # '
             'and +) default: %(default)s)')
    parser.add_argument(
        '-T', '--feedback',
        action='store',
        type=str,
        default="/lutron/feedback",
        help='Topic on broker to publish feedback to (default: '
             '%(default)s)')
    parser.add_argument(
        '-b', '--broker',
        action='store',
        type=str,
        default=None,
        help='ipaddress of MQTT broker (default: %(default)s)')
    parser.add_argument(
        '-p', '--port',
        action='store',
        type=int,
        default=1883,
        help='MQTT broker port number (default: %(default)s)')
    parser.add_argument(
        '-U', '--user',
        action='store',
        type=str,
        default=None,
        help='MQTT broker user name (default: %(default)s)')
    parser.add_argument(
        '-P', '--passwd',
        action='store',
        type=str,
        default=None,
        help='MQTT broker password (default: %(default)s)')
    parser.add_argument(
        '-poll', '--poll_interval',
        action='store',
        type=int,
        default=0,
        help='Polling interval (seconds) (0=off) (default: %(default)s)')
    parser.add_argument(
        '-pm', '--poll_methods',
        nargs='*',
        action='store',
        type=str,
        default='status',
        help='Polling method (default: %(default)s)')
    parser.add_argument(
        '-l', '--log',
        action='store',
        type=str,
        default="./lutron.log",
        help='path/name of log file (default: %(default)s)')
    parser.add_argument(
        '-J', '--json_out',
        action='store_true',
        default = False,
        help='publish topics as json (vs individual topics) (default: %(default)s)')
    parser.add_argument(
        '-D', '--debug',
        action='store_true',
        default = False,
        help='debug mode')
    parser.add_argument(
        '--version',
        action='version',
        version="%(prog)s ({})".format(__version__),
        help='Display version of this program')
    return parser.parse_args()
    
def setup_logger(logger_name, log_file, level=logging.DEBUG, console=False):
    try: 
        l = logging.getLogger(logger_name)
        formatter = logging.Formatter('[%(asctime)s][%(levelname)5.5s](%(name)-20s) %(message)s')
        if log_file is not None:
            fileHandler = logging.handlers.RotatingFileHandler(log_file, mode='a', maxBytes=10000000, backupCount=10)
            fileHandler.setFormatter(formatter)
        if console == True:
            #formatter = logging.Formatter('[%(levelname)1.1s %(name)-20s] %(message)s')
            streamHandler = logging.StreamHandler()
            streamHandler.setFormatter(formatter)

        l.setLevel(level)
        if log_file is not None:
            l.addHandler(fileHandler)
        if console == True:
          l.addHandler(streamHandler)
             
    except Exception as e:
        print("Error in Logging setup: %s - do you have permission to write the log file??" % e)
        sys.exit(1)
            
if __name__ == "__main__":
    arg = parse_args()
    
    if arg.debug:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO
        
    while True:
        time.sleep(1)
        if os.system('systemctl is-active --quiet emqx') == 0:
            break
        else:
            print("waiting on EMQX")	

    #setup logging
    log_name = 'Main'
    setup_logger(log_name, arg.log, level=log_level,console=True)
    setup_logger('pylutron_caseta', arg.log, level=log_level,console=True)

    log = logging.getLogger(log_name)
    
    log.info("*******************")
    log.info("* Program Started *")
    log.info("*******************")
    
    log.debug('Debug Mode')

    log.info("{} Version: {}".format(sys.argv[0], __version__))

    log.info("Python Version: {}".format(sys.version.replace('\n','')))
    
    if arg.poll_interval:
        if not arg.poll_methods:
            arg.poll_interval = 0
        else:
            log.info(f'Polling {arg.poll_methods} every {arg.poll_interval}s')

    loop = asyncio.get_event_loop()
    loop.set_debug(arg.debug)
    try:
        if arg.broker:
            r = Caseta( arg.bridgeip,
                        ip=arg.broker,
                        port=arg.port,
                        user=arg.user,
                        password=arg.passwd,
                        pubtopic=arg.feedback,
                        topic=arg.topic,
                        name="caseta",
                        poll=(arg.poll_interval, arg.poll_methods),
                        json_out=arg.json_out,
                        #log=log
                        )
            asyncio.gather(r._connect(), return_exceptions=True)
            loop.run_forever()
        else:
            r = Caseta(arg.bridgeip, log=log)
            log.info(loop.run_until_complete(r._connect()))
            
    except (KeyboardInterrupt, SystemExit):
        log.info("System exit Received - Exiting program")
        if arg.broker:
            r.stop()
        
    finally:
        pass
