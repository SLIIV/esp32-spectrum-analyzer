import sys, time, re
from collections import deque
import numpy as np
import serial
import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets, QtCore

# ---------- Настройки ----------
PORT = 'COM3'                   # ← замени на свой порт
BAUD = 1000000
N_SAMPLES = 2048                 # малое n = высокий FPS
FREQ_MHZ = 2462
RATE = 0                        # 0=80МГц, 1=40МГц, 6=16МГц
FS = 80e6                       # частота дискретизации для rate=0
CAP_FORMAT = 20                 # 16 = 8 бит I/Q, 20 = 10 бит I/Q
WF_HEIGHT = 600                 # число кадров в истории водопада
AVG_COUNT = 18                  # сколько кадров усреднять для СПЕКТРА
V_MIN, V_MAX = 15, 90.0        # dB-уровни для водопада (под реальный диапазон)
ADAPTIVE_LEVELS = False          # True = авто-подстройка уровней по перцентилям
DIAG = True                     # печатать диагностику

pg.setConfigOptions(useOpenGL=True, antialias=False, imageAxisOrder='row-major')


# ---------- Рабочий поток ----------
class SerialWorker(QtCore.QThread):
    data_ready = QtCore.pyqtSignal(np.ndarray)      # линейная мощность кадра
    error = QtCore.pyqtSignal(str)

    def __init__(self, port, baud, n, rate, fmt, freq_mhz):
        super().__init__()
        self.port, self.baud = port, baud
        self.n, self.rate, self.fmt = n, rate, fmt
        self.running = True
        self.freq = freq_mhz
        self._t, self._cnt = time.time(), 0
        self._window = np.hanning(n).astype(np.float32)

    def run(self):
        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.5)
            time.sleep(0.4)
            ser.write(b"FREQ {self.freq}\n");      time.sleep(0.05)
            ser.write(b"GAIN MANUAL 60\n"); time.sleep(0.05)
            ser.write(f"FREQ {self.freq}\n".encode())
            time.sleep(0.1)
            resp = ser.read(ser.in_waiting)
            print(f"FREQ {self.freq} → {resp!r}")
            ser.reset_input_buffer()
        except Exception as e:
            self.error.emit(f"Port error: {e}")
            return

        while self.running:
            try:
                iq = self.capture(ser)
                if iq is None:
                    continue
                iq = iq - iq.mean()                            # убираем DC
                iq_w = iq * self._window                       # Hann window
                spec = np.fft.fftshift(np.fft.fft(iq_w))
                power = (np.abs(spec) ** 2).astype(np.float32) # линейная мощность
                self.data_ready.emit(power)

                self._cnt += 1
                now = time.time()
                if now - self._t > 1.0:
                    print(f"FPS: {self._cnt}")
                    self._cnt, self._t = 0, now
            except Exception as e:
                self.error.emit(str(e))
                time.sleep(0.02)
        ser.close()

    def capture(self, ser):
        cap = "CAP20" if self.fmt == 20 else "CAP16"
        ser.write(f"{cap} {self.n} {self.rate}\n".encode())

        # Читаем заголовок кусками
        header = b''
        while b'\n' not in header:
            chunk = ser.read(64)
            if not chunk:
                return None
            header += chunk

        line, _, rest = header.partition(b'\n')
        m = re.match(rb'DATA (\d+) ([0-9a-fA-F]+) (\d+)', line)
        if not m:
            return None

        n = int(m.group(1))
        total = (n * self.fmt + 7) // 8

        # Дочитываем данные
        buf = bytearray(rest)
        while len(buf) < total:
            chunk = ser.read(min(2048, total - len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
        if len(buf) < total:
            return None

        if self.fmt == 20:
            return self.parse20(bytes(buf[:total]), n)
        return self.parse16(bytes(buf[:total]), n)

    @staticmethod
    def parse20(data, n):
        """CAP20: 10 бит I + 10 бит Q, 5 байт на 2 сэмпла."""
        arr = np.frombuffer(data, dtype=np.uint8)
        m = (len(arr) // 5) * 5
        g = arr[:m].reshape(-1, 5).astype(np.uint32)
        b0, b1, b2, b3, b4 = g[:, 0], g[:, 1], g[:, 2], g[:, 3], g[:, 4]
        a = b0 | (b1 << 8) | ((b2 & 0x0F) << 16)
        b = ((b2 >> 4) & 0x0F) | (b3 << 4) | (b4 << 12)
        w = np.empty(a.size * 2, dtype=np.uint32)
        w[0::2] = a
        w[1::2] = b
        w = w[:n]
        i_ = (w & 0x3FF).astype(np.int32)
        q_ = ((w >> 10) & 0x3FF).astype(np.int32)
        i_ = np.where(i_ & 0x200, i_ - 0x400, i_)
        q_ = np.where(q_ & 0x200, q_ - 0x400, q_)
        return (q_ + 1j * i_).astype(np.complex64)

    @staticmethod
    def parse16(data, n):
        """CAP16: 8 бит I + 8 бит Q, знаковые (two's complement)."""
        arr = np.frombuffer(data, dtype=np.uint8)[:n * 2].astype(np.int32)
        arr = np.where(arr & 0x80, arr - 0x100, arr)   # sign extend 8→32
        a = arr.reshape(-1, 2)
        return (a[:, 0] + 1j * a[:, 1]).astype(np.complex64)


# ---------- GUI ----------
app = pg.mkQApp("ESP-SDR Real-time")
win = pg.GraphicsLayoutWidget(show=True, title="ESP-SDR Real-time")
win.resize(1400, 1350)
win.ci.layout.setRowStretchFactor(0, 1)    # спектр — 1 часть
win.ci.layout.setRowStretchFactor(1, 2)

# --- Спектр ---
plot_spec = win.addPlot(row=0, col=0, title="Спектр (усреднённый)")
plot_spec.setLabel('left', 'Мощность', units='dB')
plot_spec.setLabel('bottom', 'Частота', units='МГц')
plot_spec.showGrid(x=True, y=True, alpha=0.3)
plot_spec.enableAutoRange(axis='y')
curve = plot_spec.plot(pen=pg.mkPen('y', width=1))

# --- Водопад (горизонтальный, новое слева) ---
plot_wf = win.addPlot(row=1, col=0, title="Водопад (сырой)")
plot_wf.setLabel('left', 'Время')
plot_wf.setLabel('bottom', 'Частота', units='МГц')
plot_wf.showGrid(x=True, y=True, alpha=0.3)

img = pg.ImageItem()
plot_wf.addItem(img)
img.setColorMap(pg.colormap.get('turbo'))     # контрастная палитра

freqs = (np.fft.fftshift(np.fft.fftfreq(N_SAMPLES, d=1/FS)) / 1e6) + FREQ_MHZ
WIDTH = freqs[-1] - freqs[0]

# Горизонтальный водопад: массив (freq, time)
# waterfall = np.full((N_SAMPLES, WF_HEIGHT), V_MIN, dtype=np.float32)
# rect = QtCore.QRectF(0, freqs[0], WF_HEIGHT, WIDTH)
# waterfall = np.full((WF_HEIGHT, N_SAMPLES), V_MIN, dtype=np.float32)
# rect = QtCore.QRectF(freqs[0], 0, WIDTH, WF_HEIGHT)
waterfall = np.full((WF_HEIGHT, N_SAMPLES), V_MIN, dtype=np.float32)
rect = QtCore.QRectF(freqs[0], 0, WIDTH, WF_HEIGHT)

img.setImage(waterfall, autoLevels=False, levels=(V_MIN, V_MAX))
img.setRect(rect)

# img.setImage(waterfall, autoLevels=False, levels=(V_MIN, V_MAX), rect=rect)


# Буфер усреднения спектра
avg_buf = deque(maxlen=AVG_COUNT)


def update_plots(power):
    """power — линейная мощность ОДНОГО кадра."""
    global waterfall

    mid = len(power) // 2

    # ========== ВОДОПАД: сырой кадр БЕЗ усреднения ==========
    spec_db_raw = (10 * np.log10(power + 1e-12)).astype(np.float32)
    spec_db_raw[mid-2:mid+3] = np.median(spec_db_raw)   # медиана вместо NaN

    # Уровни для палитры
    if ADAPTIVE_LEVELS:
        lo = float(np.percentile(spec_db_raw, 5))
        hi = float(np.percentile(spec_db_raw, 95))
        if hi - lo < 5.0:
            hi = lo + 5.0
    else:
        lo, hi = V_MIN, V_MAX

    # waterfall[:, 1:] = waterfall[:, :-1]                # сдвиг вправо
    # waterfall[:, 0] = spec_db_raw                       # новое слева
    # waterfall[1:] = waterfall[:-1]     # сдвиг вниз
    # waterfall[0] = spec_db_raw          # новое сверху
    waterfall[:-1] = waterfall[1:]
    waterfall[-1] = spec_db_raw

    img.setImage(waterfall, autoLevels=False, levels=(lo, hi))
    img.setRect(rect)

    # img.setImage(waterfall, autoLevels=False,
    #              levels=(lo, hi), rect=rect)

    # ========== Диагностика ==========
    if DIAG:
        if not hasattr(update_plots, '_n'):
            update_plots._n = 0
        update_plots._n += 1
        if update_plots._n == 50:
            print("=" * 60)
            print(f"[DIAG] power: min={power.min():.3e}, max={power.max():.3e}")
            print(f"[DIAG] spec_db_raw: min={spec_db_raw.min():.1f}, "
                  f"max={spec_db_raw.max():.1f}, mean={spec_db_raw.mean():.1f}, "
                  f"std={spec_db_raw.std():.1f}")
            print(f"[DIAG] levels: {lo:.1f} / {hi:.1f}")
            print("=" * 60)

    # ========== СПЕКТР: усреднение по мощности ==========
    avg_buf.append(power)
    power_avg = np.mean(avg_buf, axis=0)
    spec_db_avg = (10 * np.log10(power_avg + 1e-12)).astype(np.float32)

    spec_db_disp = spec_db_avg.copy()
    spec_db_disp[mid-2:mid+3] = np.nan                  # разрыв в DC

    curve.setData(freqs, spec_db_disp, connect='finite')


# ---------- Запуск ----------
worker = SerialWorker(PORT, BAUD, N_SAMPLES, RATE, CAP_FORMAT, FREQ_MHZ)
worker.data_ready.connect(update_plots)
worker.error.connect(lambda m: print(f"ERR: {m}"))
worker.start()

if __name__ == '__main__':
    QtWidgets.QApplication.instance().exec()
    worker.running = False
    worker.wait()
