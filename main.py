import os
import time

import pyaudio
import torch
from PyQt6 import QtWidgets, uic, QtGui
from PyQt6.QtCore import QThread, pyqtSignal, QAbstractItemModel
from PyQt6.QtCore import Qt, QAbstractItemModel, QModelIndex
from PyQt6.QtGui import QTextCursor


import speech_recognition as sr
from speech_recognition import AudioData
import queue
from translate import Translator
import sounddevice as sd

audio_queue = queue.Queue()
buff_audio_queue = queue.Queue()
#buff_audio_word = queue.Queue()
text_queue = queue.Queue()
voice_queue = queue.Queue()

# Получаем абсолютный путь до текущего скрипта
script_path = os.path.abspath(__file__)
# Получаем путь до директории, в которой находится текущий скрипт
script_dir = os.path.dirname(script_path)


class VoiceThread(QThread):
    def __init__(self, pyaudio_obj, leng_voice, output_device_index):
        QThread.__init__(self)
        self.audio_object = pyaudio_obj #объект PyAudio
        self.leng_voice = leng_voice
        self.output_device = output_device_index

    def run(self):
        device = torch.device('cpu')
        torch.set_num_threads(4)
        local_file = script_dir + '/model' + self.leng_voice + '.pt'
        sample_rate = 48000

        if not os.path.isfile(local_file):
            if self.leng_voice == 'RU':
                speaker = 'random'
                torch.hub.download_url_to_file('https://models.silero.ai/models/tts/ru/v3_1_ru.pt', local_file)
            if self.leng_voice == 'EN':
                speaker = 'en_58'
                torch.hub.download_url_to_file('https://models.silero.ai/models/tts/en/v3_en.pt', local_file)

        model = torch.package.PackageImporter(local_file).load_pickle("tts_models", "model")
        model.to(device)

        while True:
            text_voice = voice_queue.get()
            audio = model.apply_tts(text=text_voice,
                                    speaker=speaker,
                                    sample_rate=sample_rate)
            audio_np = audio.cpu().detach().numpy()
            sd.play(audio_np, samplerate=sample_rate, device=self.output_device)
            sd.wait()


class StreamThread(QThread):
    def __init__(self, pyaudio_obj, input_device_index, output_device_index):
        QThread.__init__(self)
        self.audio_object = pyaudio_obj #объект PyAudio
        self.input_device = input_device_index
        self.output_device = output_device_index

    def run(self):
        # Устанавливаем параметры звуковых потоков
        chunk = 1024  # Размер блока для чтения/записи данных
        sample_format = pyaudio.paInt16  # Формат звука
        channels = 2  # Количество каналов (стерео)
        fs = 44100  # Частота дискретизации

        # Открываем звуковой поток для чтения данных с выбранного устройства ввода
        stream_in = self.audio_object.open(format=sample_format,
                           channels=channels,
                           rate=fs,
                           frames_per_buffer=chunk,
                           input=True,
                           input_device_index=self.input_device)

        # Открываем звуковой поток для записи данных на выбранное устройство вывода
        stream_out = self.audio_object.open(format=sample_format,
                            channels=channels,
                            rate=fs,
                            frames_per_buffer=chunk,
                            output=True,
                            output_device_index=self.output_device)

        # Читаем данные с выбранного устройства ввода и записываем их на выбранное устройство вывода
        while True:
            data = stream_in.read(chunk)
            stream_out.write(data)


class TranslateThread(QThread):
    translate = pyqtSignal(str)

    def __init__(self, source_lang, target_lang):
        QThread.__init__(self)
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.gogle_translator = Translator(from_lang=source_lang, to_lang=target_lang)

    def run(self):
        while True:
            source_text = text_queue.get()
            source_text_array = []
            if len(source_text) > 500:
                index = source_text[:500].rfind(' ')
                source_text_array.append(source_text[0:index])
                source_text_array.append(source_text[index:])
            else:
                source_text_array.append(source_text)

            #translated_text = deepl.translate(source_text, target_lang=self.target_lang, source_lang=self.source_lang)
            translated_text = ''
            for src_txt in source_text_array:
                if len(translated_text) > 0:
                    translated_text = translated_text + ', '
                translated_text = translated_text + self.gogle_translator.translate(src_txt)

            self.translate.emit(translated_text.lower())


class RecognizeThread(QThread):
    transcript = pyqtSignal(str, int)

    def __init__(self, language):
        QThread.__init__(self)
        self.r = sr.Recognizer()
        self.language = language
        self.audio_data = None
        self.last_translate = ''
        self.end_offer = False
        #self.counter_for_end = 2

    def run(self):
        while True:
            #self.transcript.emit('.')
            if audio_queue.qsize() > 0 or buff_audio_queue.qsize() < 2:
                buff_audio_queue.put(audio_queue.get())

            if buff_audio_queue.qsize() > 5:
                buff_audio_queue.get()


            if buff_audio_queue.qsize() > 1:
                # Получаем список объектов из очереди
                audio_list = list(buff_audio_queue.queue)
                # Объединяем объекты в список байтов
                audio_data = b''.join([audio.get_raw_data() for audio in audio_list])
                # Создаем новый объект AudioData из объединенных байтов
                audio_pass = AudioData(audio_data, sample_rate=44100, sample_width=2)
            else:
                audio_pass = buff_audio_queue.queue[0]

            try:
                #text = self.r.recognize_whisper(audio_pass, language=self.language)
                text = self.r.recognize_google(audio_pass, language=self.language)
                text = text.lower()
            except:
                text = ''
                pass


            if len(text) == 0 and len(self.last_translate) > 0:
                while not buff_audio_queue.empty():
                    buff_audio_queue.get()
                self.last_translate = ''
                self.transcript.emit('.', 0)
                continue


            #print('self.last_translate:' + self.last_translate)
            #print('text:' + text)

            len_txt = len(self.last_translate)
            key_search = ''
            add_text = ''
            last_words = self.last_translate.split()
            last_count = len(last_words)

            if last_count > 6:
                midle_count = last_count - 1
                # Поиск ключа
                while midle_count >= 1:
                    #print(f'midle_count:{midle_count}')
                    key_search = last_words[midle_count - 1] + ' ' + last_words[midle_count]
                    #print('kay_search:' + key_search)
                    index_add = text.find(key_search)
                    if index_add < 0:
                        midle_count = midle_count - 1
                    else:
                        break
                #print('kay_search result:' + key_search)
                index_sr = self.last_translate.find(key_search)
                #print(f'index_add: {index_add}')
                #print(f'index_sr:{index_sr}')
                if index_add > 0 and index_sr > 0:
                    text_compar_last = self.last_translate[index_sr + len(key_search):]
                    text_compar = text[index_add + len(key_search):]
                    #print('text_compar_last:' + text_compar_last)
                    #print('text_compar:' + text_compar)
                    if len(text_compar) - len(text_compar_last) > 0:
                        add_text = text_compar
                        len_txt = len(text_compar_last)
            else:
                if len(text) > 0:
                    words_add = text.split()
                    key_search = words_add[0]
                add_text = text

            if len(add_text) > 0:
                self.last_translate = text
                self.transcript.emit(add_text, len_txt)

            #print('add_text:' + add_text)



class ListenerThread(QThread):
    finished = pyqtSignal(AudioData)

    def __init__(self, mic_index, language):
        QThread.__init__(self)
        self.r = sr.Recognizer()
        #self.r.energy_threshold = 4000
        #self.r.pause_threshold = 0.000001  # Длительность тишины в секундах, чтобы закончить запись речи
        #self.r.non_speaking_duration = self.r.pause_threshold
        self.mic_index = mic_index
        self.language = language

    def run(self):
        mic = sr.Microphone(device_index=self.mic_index)
        with mic as source:
            #print("Говорите...")
            self.r.adjust_for_ambient_noise(source)
            while True:
                audio = self.r.record(source, duration=1)
                #audio = self.r.listen(source, phrase_time_limit=1)
                self.finished.emit(audio)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi(script_dir + "/ui/mainwindow.ui", self)

        self.listener = None
        self.worker = None
        self.translater = None
        self.streamer = None
        self.talker = None
        self.end_offer = False


        self.pobj = pyaudio.PyAudio()  # Создаем объект PyAudio
        # Получаем количество устройств ввода и вывода
        input_device_count = self.pobj.get_device_count()
        output_device_count = self.pobj.get_device_count()

        self.source_stream_combo.clear()
        for i in range(input_device_count):
            device_info = self.pobj.get_device_info_by_index(i)
            if device_info['maxInputChannels'] > 0:
                self.source_stream_combo.addItem(f"{i}: {device_info['name'].encode('cp1251').decode('utf-8')}")
                index = self.source_stream_combo.model().index(self.source_stream_combo.count() - 1, 0)
                self.source_stream_combo.model().setData(index, i, role=Qt.ItemDataRole.UserRole + 1)

        self.target_stream_combo.clear()
        self.target_voice_combo.clear()
        for i in range(output_device_count):
            device_info = self.pobj.get_device_info_by_index(i)
            if device_info['maxOutputChannels'] > 0:
                self.target_stream_combo.addItem(f"{i}: {device_info['name'].encode('cp1251').decode('utf-8')}")
                index = self.target_stream_combo.model().index(self.target_stream_combo.count() - 1, 0)
                self.target_stream_combo.model().setData(index, i, role=Qt.ItemDataRole.UserRole + 1)

                self.target_voice_combo.addItem(f"{i}: {device_info['name'].encode('cp1251').decode('utf-8')}")
                index = self.target_voice_combo.model().index(self.target_voice_combo.count() - 1, 0)
                self.target_voice_combo.model().setData(index, i, role=Qt.ItemDataRole.UserRole + 1)

        self.devices_combo.clear()
        for i, device_name in enumerate(sr.Microphone.list_microphone_names()):
            self.devices_combo.addItem(f"{i}: {device_name.encode('cp1251').decode('utf-8')}")

        self.language_combo.clear()
        self.language_combo.addItems(['ru-RU', 'en-US', 'es-ES', 'fr-FR'])

        self.source_lang_combo.clear()
        self.source_lang_combo.addItems(['EN', 'RU'])

        self.target_lang_combo.clear()
        self.target_lang_combo.addItems(['EN', 'RU'])

        self.lang_voice_combo.clear()
        self.lang_voice_combo.addItems(['EN', 'RU'])

        self.start_button.clicked.connect(self.start_listening)
        self.stop_button.clicked.connect(self.stop_listening)

        self.start_translate.clicked.connect(self.start_translater)
        self.stop_translate.clicked.connect(self.stop_translater)

        self.start_stream_button.clicked.connect(self.start_stream)
        self.stop_stream_button.clicked.connect(self.stop_stream)

        self.start_voice_button.clicked.connect(self.start_voice)
        self.stop_voice_button.clicked.connect(self.stop_voice)

    def start_voice(self):
        if not self.talker:
            selected_row = self.target_voice_combo.currentIndex()
            selected_index = self.target_voice_combo.model().index(selected_row, 0)
            output_device_idx = self.target_voice_combo.model().data(selected_index, Qt.ItemDataRole.UserRole + 1)

            language = self.lang_voice_combo.currentText()

            self.talker = VoiceThread(self.pobj, language, output_device_idx)
            self.talker.start()
            self.label_status_voice.setText('Voice running')
            #self.label_status.setStyleSheet("color: blue")

    def stop_voice(self):
        if self.talker:
            self.talker.terminate()
            self.talker = None
        self.label_status_voice.setText('Voice stopped')

    def start_stream(self):
        if not self.streamer:
            selected_row = self.source_stream_combo.currentIndex()
            selected_index = self.source_stream_combo.model().index(selected_row, 0)
            input_device_idx = self.source_stream_combo.model().data(selected_index, Qt.ItemDataRole.UserRole + 1)

            selected_row = self.target_stream_combo.currentIndex()
            selected_index = self.target_stream_combo.model().index(selected_row, 0)
            output_device_idx = self.target_stream_combo.model().data(selected_index, Qt.ItemDataRole.UserRole + 1)

            self.streamer = StreamThread(self.pobj, input_device_idx, output_device_idx)
            self.streamer.start()
            self.label_status_stream.setText('Stream running')
            #self.label_status.setStyleSheet("color: blue")

    def stop_stream(self):
        if self.streamer:
            self.streamer.terminate()
            self.streamer = None
        self.label_status_stream.setText('Stream stopped')
        #self.label_status.setStyleSheet("color: red")

    def start_listening(self):
        if not self.listener:
            device_idx = self.devices_combo.currentIndex()
            language = self.language_combo.currentText()
            self.listener = ListenerThread(device_idx, language)
            self.listener.finished.connect(self.on_listener_finished)

            self.worker = RecognizeThread(language)
            self.worker.transcript.connect(self.on_worker_finished)

            self.worker.start()
            self.listener.start()
            self.label_status.setText('Recognition running')
            #self.label_status.setStyleSheet("color: blue")

    def stop_listening(self):
        if self.listener:
            self.listener.terminate()
            self.listener = None
        if self.worker:
            self.worker.terminate()
            self.worker = None

        self.label_status.setText('Recognition stopped')
        #self.label_status.setStyleSheet("color: red")

    def start_translater(self):
        if not self.translater:
            source_lang = self.source_lang_combo.currentText()
            target_lang = self.target_lang_combo.currentText()
            self.translater = TranslateThread(source_lang, target_lang)
            self.translater.translate.connect(self.on_translate_finished)
            self.translater.start()
            self.label_status_translate.setText('Translate running')
            # self.label_status.setStyleSheet("color: blue")

    def stop_translater(self):
        if self.translater:
            self.translater.terminate()
            self.translater = None

        self.label_status_translate.setText('Translate stopped')
        # self.label_status.setStyleSheet("color: red")

    def on_listener_finished(self, audio):
        audio_queue.put(audio)

    def on_worker_finished(self, transcript_txt, pos_cursor):
        curr_text = self.transcript_textedit.toPlainText()
        len_text = len(curr_text)
        print(f'transcript_txt = {transcript_txt}')
        if len(transcript_txt) > 0:
            cursor = QTextCursor(self.transcript_textedit.document())
            cursor.setPosition(len_text)
            if transcript_txt == '.':
                cursor.insertText(transcript_txt)
                self.translated_textedit.append('')
                print(f'append')
            else:
                cursor.movePosition(QTextCursor.MoveOperation.Left, mode=QTextCursor.MoveMode.KeepAnchor, n=pos_cursor)
                cursor.insertText(transcript_txt)

        self.translated_textedit.verticalScrollBar().setValue(self.transcript_textedit.verticalScrollBar().maximum())


    def on_translate_finished(self, translate_text):
        self.translated_textedit.insertPlainText(translate_text)
        self.translated_textedit.insertPlainText('.')
        if self.talker:
            voice_queue.put(translate_text)

        self.translated_textedit.append('')
        self.translated_textedit.append('')
        self.translated_textedit.verticalScrollBar().setValue(self.transcript_textedit.verticalScrollBar().maximum())


if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
