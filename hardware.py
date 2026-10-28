"""Hardware abstraction. Simulation mode is used on non-Pi computers or when enabled."""
import os
import random
import threading
import time

SIMULATION = os.getenv("DRIPFERTI_SIMULATION", "false").lower() in ("1", "true", "yes")


class Hardware:
    def __init__(self):
        self.simulation = SIMULATION
        self._lock = threading.Lock()
        self._pulse_count = 0
        self._flow_last_pulse = time.monotonic()
        self._valve_on = False
        self._total_liters = 0.0
        self._last_flow_lpm = 0.0
        self._last_sample_time = time.monotonic()
        self._soil_voltage = None
        self._soil_percent = None
        self._gpio = None
        self._flow_input = None
        self._relay = None
        self._adc = None
        if not self.simulation:
            self._init_real_hardware()

    def _init_real_hardware(self):
        from gpiozero import DigitalInputDevice, OutputDevice
        import board
        import busio
        import adafruit_ads1x15.ads1115 as ADS
        from adafruit_ads1x15.analog_in import AnalogIn

        i2c = busio.I2C(board.SCL, board.SDA)
        address = int(os.getenv("ADS1115_ADDRESS", "0x48"), 0)
        self._adc = ADS.ADS1115(i2c, address=address)
        self._adc.gain = int(os.getenv("ADS1115_GAIN", "1"))
        self._soil_channel = AnalogIn(self._adc, ADS.P0)
        flow_pin = int(os.getenv("FLOW_GPIO", "22"))
        relay_pin = int(os.getenv("RELAY_GPIO", "17"))
        self._flow_input = DigitalInputDevice(flow_pin, pull_up=os.getenv("FLOW_PULL_UP", "true").lower() == "true", bounce_time=None)
        self._flow_input.when_activated = self._on_flow_pulse
        active_low = os.getenv("RELAY_ACTIVE_LOW", "true").lower() == "true"
        self._relay = OutputDevice(relay_pin, active_high=not active_low, initial_value=False)
        self.valve_off()

    def _on_flow_pulse(self):
        with self._lock:
            self._pulse_count += 1
            self._flow_last_pulse = time.monotonic()

    def read_soil(self):
        if self.simulation:
            if self._soil_percent is None:
                self._soil_percent = random.uniform(25, 65)
            self._soil_percent = max(5, min(90, self._soil_percent + random.uniform(-1.4, 1.0)))
            dry = float(os.getenv("SOIL_DRY_V", "2.8")); wet = float(os.getenv("SOIL_WET_V", "1.2"))
            voltage = dry + (wet - dry) * self._soil_percent / 100.0
        else:
            voltage = float(self._soil_channel.voltage)
            dry = float(os.getenv("SOIL_DRY_V", "2.8")); wet = float(os.getenv("SOIL_WET_V", "1.2"))
            if abs(dry - wet) < 0.05:
                raise ValueError("SOIL_DRY_V and SOIL_WET_V must differ by at least 0.05 V")
            percent = (dry - voltage) / (dry - wet) * 100.0
            self._soil_percent = max(0.0, min(100.0, percent))
        self._soil_voltage = voltage
        return {"moisture_percent": round(float(self._soil_percent), 2), "raw_voltage_v": round(float(voltage), 3)}

    def sample_flow(self, interval_seconds):
        pulses_per_liter = float(os.getenv("FLOW_PULSES_PER_LITER", "450"))
        if pulses_per_liter <= 0:
            raise ValueError("FLOW_PULSES_PER_LITER must be positive")
        if self.simulation:
            pulses = random.randint(0, 4) if self._valve_on else 0
        else:
            with self._lock:
                pulses = self._pulse_count
                self._pulse_count = 0
        liters = pulses / pulses_per_liter
        lpm = liters * 60.0 / max(0.1, interval_seconds)
        self._total_liters += liters
        self._last_flow_lpm = lpm
        return {"flow_lpm": round(lpm, 3), "total_liters": round(self._total_liters, 3), "pulse_count": pulses}

    def flow_is_stale(self):
        if self.simulation:
            return False
        with self._lock:
            last = self._flow_last_pulse
        timeout = float(os.getenv("FLOW_TIMEOUT_SECONDS", "8"))
        return self._valve_on and (time.monotonic() - last) >= timeout

    def valve_on(self):
        if self._relay is not None:
            self._relay.on()
        self._valve_on = True

    def valve_off(self):
        if self._relay is not None:
            self._relay.off()
        self._valve_on = False

    @property
    def valve_status(self):
        return "ON" if self._valve_on else "OFF"

    def close(self):
        try:
            self.valve_off()
        finally:
            if self._flow_input is not None:
                self._flow_input.close()
            if self._relay is not None:
                self._relay.close()
