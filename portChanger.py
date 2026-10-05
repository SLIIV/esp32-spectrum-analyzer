import serial, time

PORT = 'COM3'
ser = serial.Serial(PORT, 2000000, timeout=1)
time.sleep(0.4)

ser.write(b'BAUD 1000000\n')
time.sleep(0.5)
print("ответ:", ser.read(ser.in_waiting))
ser.close()

# теперь открываем на 1М
ser = serial.Serial(PORT, 1000000, timeout=1)
time.sleep(0.3)
ser.write(b'INFO\n')
time.sleep(0.3)
print("INFO после смены:", ser.read(ser.in_waiting))
ser.close()
