# dialog.py

import json
import os
import shutil
import re
import urllib.parse
import base64
import logging
from PyQt6.QtCore import QTimer
from aqt import mw
from aqt.qt import *
from aqt.utils import showInfo, showWarning
from aqt.webview import QWebEngineView
from anki.utils import strip_html
from .highlighter import HtmlTagHighlighter
from .media_manager import MediaManagerDialog
from .visualizar import VisualizarCards
from .utils import CONFIG_FILE

# Configuração de logging
logging.basicConfig(filename="delimitadores.log", level=logging.DEBUG)

class CustomDialog(QDialog):
    def __init__(self, parent=None):
        if not mw:
            showWarning("A janela principal do Anki não está disponível!")
            return
        logging.debug("Inicializando CustomDialog")
        super().__init__(mw, Qt.WindowType.Window | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.WindowMaximizeButtonHint)

        #super().__init__(parent)
        self.media_dialog = None  # Adicione esta linha

        self.visualizar_dialog = None
        self.last_search_query = ""
        self.last_search_position = 0
        self.zoom_factor = 1.0
        self.cloze_2_count = 1
        self.initial_tags_set = False
        self.initial_numbering_set = False
        self.media_files = []
        self.current_line = 0
        self.previous_text = ""
        self.last_edited_line = -1
        self.save_timer = QTimer(self)  # Debounce
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self._save_in_real_time)
        self.is_dark_theme = False
        self.field_mappings = {}  # Mapeamento de índices para campos
        self.field_images = {}  # Imagens associadas a cada campo
        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        self.setWindowTitle("Adicionar Cards com Delimitadores")
        self.resize(1000, 600)
        main_layout = QVBoxLayout()
        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)

        # Top Widget
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)

        # Indicador de salvamento
        self.save_status_label = QLabel("Pronto", self)
        self.save_status_label.setStyleSheet("color: gray;")
        top_layout.addWidget(self.save_status_label)

        # Botão para adicionar mídia
        media_layout = QHBoxLayout()
        image_button = QPushButton("Adicionar Imagem, Som ou Vídeo", self)
        image_button.clicked.connect(self.add_image)
        media_layout.addWidget(image_button)


        # Botão para gerenciar mídia
        manage_media_button = QPushButton("Gerenciar Mídia", self)
        manage_media_button.clicked.connect(self.manage_media)
        media_layout.addWidget(manage_media_button)


        export_html_button = QPushButton("Exportar para HTML", self)  # MOVIDO PARA CÁ
        export_html_button.clicked.connect(self.export_to_html)
        export_html_button.setToolTip("Exportar cards para arquivo HTML")
        media_layout.addWidget(export_html_button)


        # Botão para visualizar cards
        view_cards_button = QPushButton("Visualizar Cards", self)
        view_cards_button.clicked.connect(self.view_cards_dialog)
        media_layout.addWidget(view_cards_button)

        top_layout.addLayout(media_layout)

        # Fields Splitter (campo de texto à esquerda, pré-visualização à direita)
        self.fields_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Widget para "Digite seus cards" e "Etiquetas"
        self.cards_tags_widget = QWidget()
        cards_tags_layout = QHBoxLayout(self.cards_tags_widget)

        # Cards Group (Digite seus cards)
        self.cards_group = QWidget()
        cards_layout = QVBoxLayout(self.cards_group)
        cards_header_layout = QHBoxLayout()
        cards_label = QLabel("Digite seus cards:")
        cards_header_layout.addWidget(cards_label)

        # Botões de Cor do Texto
        for color in ["red", "blue", "green", "yellow"]:
            btn = QPushButton("A")
            btn.setStyleSheet(f"color: {color}; background-color: black;")
            btn.setFixedSize(30, 30)
            btn.clicked.connect(lambda checked, c=color: self.apply_text_color(c))
            btn.setToolTip("Aplicar cor ao texto")
            cards_header_layout.addWidget(btn)

        # Botões de Cor de Fundo
        for color in ["red", "blue", "green", "yellow"]:
            btn = QPushButton("Af")
            btn.setStyleSheet(f"background-color: {color}; color: black;")
            btn.setFixedSize(30, 30)
            btn.clicked.connect(lambda checked, c=color: self.apply_background_color(c))
            btn.setToolTip("Aplicar cor de fundo ao texto")
            cards_header_layout.addWidget(btn)

        cards_header_layout.addStretch()
        cards_layout.addLayout(cards_header_layout)




        self.txt_entrada = QTextEdit()
        self.txt_entrada.setUndoRedoEnabled(True)  # Suporte a undo/redo
        self.txt_entrada.setPlaceholderText("Digite seus cards aqui...")
        self.highlighter = HtmlTagHighlighter(self.txt_entrada.document())
        self.txt_entrada.textChanged.connect(self.schedule_save)  # Debounce
        self.txt_entrada.textChanged.connect(self.update_tags_lines)
        self.txt_entrada.cursorPositionChanged.connect(self.check_line_change)
        self.txt_entrada.installEventFilter(self)
        cards_layout.addWidget(self.txt_entrada)

        cards_tags_layout.addWidget(self.cards_group, stretch=2)

        # Etiquetas Group (ao lado de Digite seus cards)
        self.etiquetas_group = QWidget()
        etiquetas_layout = QVBoxLayout(self.etiquetas_group)
        etiquetas_header_layout = QHBoxLayout()
        self.tags_label = QLabel("Etiquetas:")
        etiquetas_header_layout.addWidget(self.tags_label)
        etiquetas_header_layout.addStretch()
        etiquetas_layout.addLayout(etiquetas_header_layout)
        self.txt_tags = QTextEdit()
        self.txt_tags.setUndoRedoEnabled(True)  # Suporte a undo/redo
        self.txt_tags.setPlaceholderText("Digite as etiquetas aqui (uma linha por card)...")
        self.txt_tags.setMaximumWidth(200)
        self.txt_tags.textChanged.connect(self.schedule_save)  # Debounce
        self.txt_tags.textChanged.connect(self.update_preview)
        self.txt_tags.installEventFilter(self)
        etiquetas_layout.addWidget(self.txt_tags)
        self.etiquetas_group.setVisible(False)
        cards_tags_layout.addWidget(self.etiquetas_group, stretch=1)

        self.fields_splitter.addWidget(self.cards_tags_widget)

        # Pré-visualização embutida à direita
        self.preview_widget = QWebEngineView()
        settings = self.preview_widget.settings()
        for attr in [QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
                     QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                     QWebEngineSettings.WebAttribute.AllowRunningInsecureContent]:
            settings.setAttribute(attr, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self.preview_widget.setMinimumWidth(300)
        self.fields_splitter.addWidget(self.preview_widget)

        self.fields_splitter.setSizes([700, 300])
        top_layout.addWidget(self.fields_splitter)

        # Opções (Numerar Tags, Repetir Tags, Mostrar/Ocultar Etiquetas, Tema)
        options_layout = QHBoxLayout()
        options_layout.addStretch()
        self.chk_num_tags = QCheckBox("Numerar Tags")
        self.chk_repetir_tags = QCheckBox("Repetir Tags")
        self.chk_num_tags.stateChanged.connect(self.update_tag_numbers)
        self.chk_num_tags.stateChanged.connect(self.schedule_save)  # Debounce
        self.chk_repetir_tags.stateChanged.connect(self.update_repeated_tags)
        self.chk_repetir_tags.stateChanged.connect(self.schedule_save)  # Debounce
        options_layout.addWidget(self.chk_num_tags)
        options_layout.addWidget(self.chk_repetir_tags)

        self.toggle_tags_button = QPushButton("Mostrar Etiquetas", self)
        self.toggle_tags_button.clicked.connect(self.toggle_tags)
        options_layout.addWidget(self.toggle_tags_button)

        self.theme_button = QPushButton("Mudar Tema", self)
        self.theme_button.clicked.connect(self.toggle_theme)
        options_layout.addWidget(self.theme_button)

        top_layout.addLayout(options_layout)
        self.vertical_splitter.addWidget(top_widget)

        # Bottom Widget
        bottom_scroll = QScrollArea()
        bottom_scroll.setWidgetResizable(True)
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)

        # Botões de Formatação
        btn_layout = QHBoxLayout()
        botoes_formatacao = [
            ("Juntar Linhas", self.join_lines, "Juntar todas as linhas (sem atalho)"),
            ("Destaque", self.destaque_texto, "Destacar texto (Ctrl+M)"),
            ("B", self.apply_bold, "Negrito (Ctrl+B)"),
            ("I", self.apply_italic, "Itálico (Ctrl+I)"),
            ("U", self.apply_underline, "Sublinhado (Ctrl+U)"),
            ("Concatenar", self.concatenate_text, "Concatenar texto (sem atalho)"),
            ("Limpar Tudo", self.clear_all, "Limpar todos os campos e configurações"),
            ("Desfazer", self.txt_entrada.undo, "Desfazer (Ctrl+Z)"),  # Undo
            ("Refazer", self.txt_entrada.redo, "Refazer (Ctrl+Y)"),  # Redo
           # ("Exportar HTML", self.export_to_html, "Exportar cards para arquivo HTML"),


        ]
        for texto, funcao, tooltip in botoes_formatacao:
            btn = QPushButton(texto)
            btn.clicked.connect(funcao)
            btn.setToolTip(tooltip)
            if texto == "Destaque":
                btn.setStyleSheet("background-color: yellow; color: black;")
            btn_layout.addWidget(btn)
        bottom_layout.addLayout(btn_layout)

        # Search Layout
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Pesquisar... Ctrl+P")
        search_layout.addWidget(self.search_input)
        search_button = QPushButton("Pesquisar", self)
        search_button.clicked.connect(self.search_text)
        search_layout.addWidget(search_button)
        self.replace_input = QLineEdit(self)
        self.replace_input.setPlaceholderText("Substituir tudo por... Ctrl+Shift+R")
        search_layout.addWidget(self.replace_input)
        replace_button = QPushButton("Substituir Tudo", self)
        replace_button.clicked.connect(self.replace_text)
        search_layout.addWidget(replace_button)
        zoom_in_button = QPushButton("+", self)
        zoom_in_button.clicked.connect(self.zoom_in)
        search_layout.addWidget(zoom_in_button)
        zoom_out_button = QPushButton("-", self)
        zoom_out_button.clicked.connect(self.zoom_out)
        search_layout.addWidget(zoom_out_button)
        bottom_layout.addLayout(search_layout)

        # Cloze Layout
        cloze_layout = QGridLayout()
        for text, func, col, tooltip in [
            ("Cloze 1 (Ctrl+Shift+D)", self.add_cloze_1, 0, "Adicionar Cloze 1 (Ctrl+Shift+D)"),
            ("Cloze 2 (Ctrl+Shift+F)", self.add_cloze_2, 1, "Adicionar Cloze 2 (Ctrl+Shift+F)"),
            ("Remover Cloze", self.remove_cloze, 2, "Remover Cloze (sem atalho)")
        ]:
            btn = QPushButton(text, self)
            btn.clicked.connect(func)
            btn.setToolTip(tooltip)
            cloze_layout.addWidget(btn, 0, col)
        bottom_layout.addLayout(cloze_layout)

        # Group Widget (Decks, Modelos, Delimitadores, Mapeamento de Campos)
        self.group_widget = QWidget()
        group_layout = QVBoxLayout(self.group_widget)

        self.group_splitter = QSplitter(Qt.Orientation.Vertical)

        decks_modelos_widget = QWidget()
        decks_modelos_layout = QVBoxLayout(decks_modelos_widget)

        self.decks_modelos_splitter = QSplitter(Qt.Orientation.Horizontal)

        decks_group = QGroupBox("Decks")
        decks_layout = QVBoxLayout(decks_group)
        self.scroll_decks, self.lista_decks = self.criar_lista_rolavel([d.name for d in mw.col.decks.all_names_and_ids()], 100)
        self.lista_decks.currentItemChanged.connect(self.schedule_save)  # Debounce
        decks_layout.addWidget(self.scroll_decks)
        self.decks_search_input = QLineEdit(self)
        self.decks_search_input.setPlaceholderText("Pesquisar decks...")
        self.decks_search_input.textChanged.connect(self.filter_decks)
        decks_layout.addWidget(self.decks_search_input)

        self.deck_name_input = QLineEdit(self)
        self.deck_name_input.setPlaceholderText("Digite o nome do novo deck...")
        decks_layout.addWidget(self.deck_name_input)
        create_deck_button = QPushButton("Criar Deck", self)
        create_deck_button.clicked.connect(self.create_deck)
        decks_layout.addWidget(create_deck_button)

        self.decks_modelos_splitter.addWidget(decks_group)

        modelos_group = QGroupBox("Modelos ou Tipos de Notas")
        modelos_layout = QVBoxLayout(modelos_group)
        self.scroll_notetypes, self.lista_notetypes = self.criar_lista_rolavel(mw.col.models.all_names(), 100)
        self.lista_notetypes.currentItemChanged.connect(self.update_field_mappings)  # Atualiza campos
        self.lista_notetypes.currentItemChanged.connect(self.update_preview)
        self.lista_notetypes.currentItemChanged.connect(self.schedule_save)  # Debounce
        modelos_layout.addWidget(self.scroll_notetypes)
        self.notetypes_search_input = QLineEdit(self)
        self.notetypes_search_input.setPlaceholderText("Pesquisar tipos de notas...")
        self.notetypes_search_input.textChanged.connect(self.filter_notetypes)
        modelos_layout.addWidget(self.notetypes_search_input)

        self.decks_modelos_splitter.addWidget(modelos_group)

        self.decks_modelos_splitter.setSizes([200, 150])

        decks_modelos_layout.addWidget(self.decks_modelos_splitter)

        self.group_splitter.addWidget(decks_modelos_widget)

        # Mapeamento de Campos
        self.fields_group = QGroupBox("Mapeamento de Campos")
        fields_layout = QVBoxLayout(self.fields_group)
        fields_layout.addWidget(QLabel("Associe cada parte a um campo:"))
        self.fields_container = QWidget()
        self.fields_container_layout = QVBoxLayout(self.fields_container)  # Mudado para VBox para incluir botões
        self.field_combo_boxes = []
        self.field_image_buttons = {}
        fields_layout.addWidget(self.fields_container)
        self.group_splitter.addWidget(self.fields_group)

        # Delimitadores
        delimitadores_widget = QWidget()
        delimitadores_layout = QVBoxLayout(delimitadores_widget)
        self.delimitadores_label = QLabel("Delimitadores:")
        delimitadores_layout.addWidget(self.delimitadores_label)
        delimitadores = [("Tab", "\t"), ("Vírgula", ","), ("Ponto e Vírgula", ";"), ("Dois Pontos", ":"),
                         ("Interrogação", "?"), ("Barra", "/"), ("Exclamação", "!"), ("Pipe", "|")]
        grid = QGridLayout()
        self.chk_delimitadores = {}
        for i, (nome, simbolo) in enumerate(delimitadores):
            chk = QCheckBox(nome)
            chk.simbolo = simbolo
            chk.stateChanged.connect(self.update_preview)
            chk.stateChanged.connect(self.schedule_save)  # Debounce
            grid.addWidget(chk, i // 4, i % 4)
            self.chk_delimitadores[nome] = chk
        delimitadores_layout.addLayout(grid)

        self.group_splitter.addWidget(delimitadores_widget)

        self.group_splitter.setSizes([150, 150, 100])

        group_layout.addWidget(self.group_splitter)

        bottom_layout.addWidget(self.group_widget)

        # Bottom Buttons
        bottom_buttons_layout = QHBoxLayout()
        self.btn_toggle = QPushButton("Ocultar Decks/Modelos/Delimitadores")
        self.btn_toggle.clicked.connect(self.toggle_group)
        bottom_buttons_layout.addWidget(self.btn_toggle)
        btn_add = QPushButton("Adicionar Cards (Ctrl+R)")
        btn_add.clicked.connect(self.add_cards)
        btn_add.setToolTip("Adicionar Cards (Ctrl+R)")
        bottom_buttons_layout.addWidget(btn_add)
        bottom_layout.addLayout(bottom_buttons_layout)
        bottom_layout.addStretch()

        bottom_scroll.setWidget(bottom_widget)
        self.vertical_splitter.addWidget(bottom_scroll)
        self.vertical_splitter.setSizes([300, 300])
        self.vertical_splitter.setChildrenCollapsible(False)
        main_layout.addWidget(self.vertical_splitter)
        self.setLayout(main_layout)

        # Atalhos
        for key, func in [
            ("Ctrl+B", "apply_bold"),
            ("Ctrl+I", "apply_italic"),
            ("Ctrl+U", "apply_underline"),
            ("Ctrl+M", "destaque_texto"),
            ("Ctrl+P", "search_text"),
            ("Ctrl+Shift+R", "replace_text"),
            ("Ctrl+=", "zoom_in"),
            ("Ctrl+-", "zoom_out"),
            ("Ctrl+Shift+D", "add_cloze_1"),
            ("Ctrl+Shift+F", "add_cloze_2"),
            ("Ctrl+R", "add_cards"),
            ("Ctrl+Z", "undo"),  # Undo
            ("Ctrl+Y", "redo"),  # Redo
        ]:
            QShortcut(QKeySequence(key), self).activated.connect(lambda f=func: self.log_shortcut(f))

        self.txt_entrada.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.txt_entrada.customContextMenuRequested.connect(self.show_context_menu)
        self.txt_entrada.setAcceptDrops(True)
        self.txt_entrada.focusInEvent = self.create_focus_handler(self.txt_entrada, "cards")
        self.txt_tags.focusInEvent = self.create_focus_handler(self.txt_tags, "tags")

    def log_shortcut(self, func_name):
        logging.debug(f"Atalho acionado: {func_name}")
        if func_name in ["undo", "redo"]:
            getattr(self.txt_entrada, func_name)()
        else:
            getattr(self, func_name)()

    def schedule_save(self):
        """Agenda o salvamento com debounce de 500ms."""
        self.save_status_label.setText("Salvando...")
        self.save_status_label.setStyleSheet("color: orange;")
        self.save_timer.start(500)

    def _save_in_real_time(self):
        """Salva todas as configurações em tempo real com backup."""
        try:
            if os.path.exists(CONFIG_FILE):
                shutil.copy2(CONFIG_FILE, CONFIG_FILE + ".bak")  # Backup
            dados = {
                'conteudo': self.txt_entrada.toPlainText(),
                'tags': self.txt_tags.toPlainText(),
                'delimitadores': {nome: chk.isChecked() for nome, chk in self.chk_delimitadores.items()},
                'deck_selecionado': self.lista_decks.currentItem().text() if self.lista_decks.currentItem() else '',
                'modelo_selecionado': self.lista_notetypes.currentItem().text() if self.lista_notetypes.currentItem() else '',
                'field_mappings': self.field_mappings,
                'field_images': self.field_images  # Salvar imagens associadas
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
            logging.debug(f"Salvamento em tempo real: {dados}")
            self.save_status_label.setText("Salvo")
            self.save_status_label.setStyleSheet("color: green;")
            QTimer.singleShot(2000, lambda: self.save_status_label.setText("Pronto") or self.save_status_label.setStyleSheet("color: gray;"))
        except Exception as e:
            logging.error(f"Erro ao salvar em tempo real: {str(e)}")
            self.save_status_label.setText("Erro ao salvar")
            self.save_status_label.setStyleSheet("color: red;")
            showWarning(f"Erro ao salvar em tempo real: {str(e)}")

    def toggle_tags(self):
        novo_estado = not self.etiquetas_group.isVisible()
        self.etiquetas_group.setVisible(novo_estado)
        self.toggle_tags_button.setText("Ocultar Etiquetas" if novo_estado else "Mostrar Etiquetas")

    def update_tags_lines(self):
        linhas_cards = self.txt_entrada.toPlainText().strip().split('\n')
        linhas_tags = self.txt_tags.toPlainText().strip().split('\n')

        if len(linhas_tags) < len(linhas_cards):
            self.txt_tags.setPlainText(self.txt_tags.toPlainText() + '\n' * (len(linhas_cards) - len(linhas_tags)))
        elif len(linhas_tags) > len(linhas_cards):
            self.txt_tags.setPlainText('\n'.join(linhas_tags[:len(linhas_cards)]))

        self.update_preview()

    def check_line_change(self):
        cursor = self.txt_entrada.textCursor()
        current_line = cursor.blockNumber()
        if current_line != self.current_line:
            self.process_media_rename()
            self.current_line = current_line
            self.last_edited_line = current_line
        self.update_preview()

    def focus_out_event(self, event):
        self.process_media_rename()
        QTextEdit.focusOutEvent(self.txt_entrada, event)

    def process_media_rename(self):
        current_text = self.txt_entrada.toPlainText()
        if self.previous_text != current_text:
            patterns = [
                r'<img src="([^"]+)"',
                r'<source src="([^"]+)"',
                r'<video src="([^"]+)"'
            ]

            previous_media = set()
            current_media = set()
            for pattern in patterns:
                previous_media.update(re.findall(pattern, self.previous_text))
                current_media.update(re.findall(pattern, current_text))

            media_dir = mw.col.media.dir()
            for old_name in previous_media:
                if old_name in self.media_files and old_name not in current_media:
                    for new_name in current_media:
                        if new_name not in previous_media and new_name not in self.media_files:
                            if os.path.exists(os.path.join(media_dir, new_name)):
                                showWarning(f"O nome '{new_name}' já existe na pasta de mídia!")
                                continue

                            try:
                                os.rename(
                                    os.path.join(media_dir, old_name),
                                    os.path.join(media_dir, new_name)
                                )
                                self.media_files[self.media_files.index(old_name)] = new_name
                                showInfo(f"Arquivo renomeado de '{old_name}' para '{new_name}' na pasta de mídia.")
                            except Exception as e:
                                showWarning(f"Erro ao renomear o arquivo: {str(e)}")
                            break

            self.previous_text = current_text

    def update_field_mappings(self):
        """Atualiza as opções de mapeamento de campos com base no modelo selecionado."""
        # Limpar widgets existentes
        for combo in self.field_combo_boxes:
            self.fields_container_layout.removeWidget(combo)
            combo.deleteLater()
        for field, btn in self.field_image_buttons.items():
            self.fields_container_layout.removeWidget(btn)
            btn.deleteLater()
        self.field_combo_boxes.clear()
        self.field_image_buttons.clear()

        if not self.lista_notetypes.currentItem():
            return

        modelo = mw.col.models.by_name(self.lista_notetypes.currentItem().text())
        campos = [fld['name'] for fld in modelo['flds']]
        num_campos = len(campos)

        # Criar um combo box e botão para cada campo do modelo
        for i in range(num_campos):
            # Combo box para mapeamento
            field_layout = QHBoxLayout()
            combo = QComboBox()
            combo.addItem(f"Parte {i+1} -> Ignorar")
            for campo in campos:
                combo.addItem(f"Parte {i+1} -> {campo}")
            if str(i) in self.field_mappings and self.field_mappings[str(i)] in campos:
                combo.setCurrentText(f"Parte {i+1} -> {self.field_mappings[str(i)]}")
            else:
                combo.setCurrentIndex(0)
            combo.currentIndexChanged.connect(self.update_field_mapping)
            self.field_combo_boxes.append(combo)
            field_layout.addWidget(combo)

            # Botão para adicionar imagens
            btn = QPushButton(f"Midia {campos[i]}")

            btn.clicked.connect(lambda checked, idx=i, campo=campos[i]: self.add_media_to_field(idx, campo))
           # btn.clicked.connect(lambda checked, idx=i, campo=campos[i]: self.add_images_to_field(idx, campo))
            self.field_image_buttons[campos[i]] = btn
            field_layout.addWidget(btn)

            self.fields_container_layout.addLayout(field_layout)

        self.update_preview()



    def update_field_mapping(self):
        """Atualiza o dicionário de mapeamento de campos quando uma seleção é alterada."""
        self.field_mappings = {}
        for i, combo in enumerate(self.field_combo_boxes):
            text = combo.currentText()
            if "Ignorar" not in text:
                campo = text.split(" -> ")[1]
                self.field_mappings[str(i)] = campo
        self.schedule_save()
        self.update_preview()



    def add_media_to_field(self, index, field_name):
        """Adiciona mídia (imagem, áudio ou vídeo) a um campo específico."""
        linhas = [linha.strip() for linha in self.txt_entrada.toPlainText().strip().split('\n') if linha.strip()]
        num_cards = len(linhas)
        if num_cards == 0:
            showWarning("Digite pelo menos um card antes de adicionar mídia!")
            return
    
        # Filtros para diferentes tipos de mídia
        arquivos, _ = QFileDialog.getOpenFileNames(
            self, 
            f"Selecionar Mídia para {field_name}", 
            "", 
            "Todos os arquivos de mídia (*.png *.jpg *.jpeg *.gif *.mp3 *.wav *.ogg *.mp4 *.webm *.avi *.mov);;"
            "Imagens (*.png *.jpg *.jpeg *.gif);;"
            "Áudio (*.mp3 *.wav *.ogg);;"
            "Vídeo (*.mp4 *.webm *.avi *.mov)"
        )
        
        if not arquivos:
            return
    
        media_dir = mw.col.media.dir()
        selected_media = []
        
        # Processar cada arquivo de mídia selecionado
        for i, caminho in enumerate(arquivos[:num_cards]):  # Limitar ao número de cards
            nome = os.path.basename(caminho)
            destino = os.path.join(media_dir, nome)
            
            # Copiar arquivo se não existir
            if not os.path.exists(destino):
                shutil.copy(caminho, destino)
            
            selected_media.append(nome)
            if nome not in self.media_files:
                self.media_files.append(nome)
            
            # Obter a linha correspondente ao card
            if i < len(linhas):
                partes = linhas[i].split(';')  # Dividir pelos delimitadores
                
                # Verificar se o índice existe
                if index < len(partes):
                    # Determinar o tipo de mídia
                    ext = os.path.splitext(nome)[1].lower()
                    media_tag = ""
                    
                    if ext in ('.png', '.jpg', '.jpeg', '.gif'):
                        media_tag = f'<img src="{nome}">'
                    elif ext in ('.mp3', '.wav', '.ogg'):
                        media_tag = f'<audio controls><source src="{nome}" type="audio/{ext[1:]}"></audio>'
                    elif ext in ('.mp4', '.webm', '.avi', '.mov'):
                        media_tag = f'<video src="{nome}" controls width="320" height="240"></video>'
                    
                    # Adicionar a mídia ao campo correspondente
                    if not re.search(r'<(img|audio|video|source)', partes[index]):
                        partes[index] = partes[index].strip() + ' ' + media_tag
                    else:
                        # Se já tem mídia, substituir
                        partes[index] = re.sub(r'<(img|audio|video|source)[^>]+>', media_tag, partes[index])
                    
                    # Atualizar a linha
                    linhas[i] = ' ; '.join(partes)
        
        # Atualizar o texto com as mídias inseridas
        self.txt_entrada.setPlainText('\n'.join(linhas))
        self.previous_text = self.txt_entrada.toPlainText()
        
        # Salvar as mídias no dicionário de field_images
        if field_name not in self.field_images:
            self.field_images[field_name] = []
        self.field_images[field_name] = selected_media[:num_cards]  # Limitar ao número de cards
        
        self.schedule_save()
        self.update_preview()





    def update_preview(self):
        try:
            cursor = self.txt_entrada.textCursor()
            self.current_line = cursor.blockNumber()

            linhas = self.txt_entrada.toPlainText().strip().split('\n')
            if not linhas or self.current_line >= len(linhas):
                self.preview_widget.setHtml("")
                return

            linha = linhas[self.current_line]
            if not linha.strip():
                self.preview_widget.setHtml("")
                return

            delimitadores = [chk.simbolo for chk in self.chk_delimitadores.values() if chk.isChecked()]
            if not delimitadores or not self.lista_decks.currentItem() or not self.lista_notetypes.currentItem():
                self.preview_widget.setHtml("")
                return

            modelo = mw.col.models.by_name(self.lista_notetypes.currentItem().text())
            campos = [fld['name'] for fld in modelo['flds']]

            linhas_tags = self.txt_tags.toPlainText().strip().split('\n')
            tags_for_current_card = []
            if self.current_line < len(linhas_tags):
                tags_for_current_card = [tag.strip() for tag in linhas_tags[self.current_line].split(',') if tag.strip()]

            card_index = self.current_line
            media_dir = mw.col.media.dir()

            def get_mime_type(file_name):
                ext = os.path.splitext(file_name)[1].lower()
                return {
                    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
                    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg', '.mp4': 'video/mp4',
                    '.webm': 'video/webm'
                }.get(ext, 'application/octet-stream')

            def replace_media_src(match, media_type="img"):
                file_name = match.group(1)
                full_path = os.path.join(media_dir, file_name)
                if not os.path.exists(full_path):
                    print(f"Arquivo não encontrado: {full_path}")
                    return match.group(0)
                try:
                    with open(full_path, 'rb') as f:
                        base64_data = base64.b64encode(f.read()).decode('utf-8')
                    mime_type = get_mime_type(file_name)
                    return f'<{media_type} src="data:{mime_type};base64,{base64_data}"' + (" controls width=\"320\" height=\"240\"" if media_type == "video" else "")
                except Exception as e:
                    print(f"Erro ao codificar {media_type} em base64: {str(e)}")
                    return match.group(0)

            cards_html = """
            <html><body style="font-family: Arial, sans-serif; background-color: #f9f9f9; padding: 10px;">
            <style>
                table {
                    border-collapse: collapse;
                    width: 100%;
                    margin: 5px 0;
                }
                th, td {
                    border: 1px solid #ddd;
                    padding: 8px;
                    text-align: left;
                    vertical-align: top;
                    width: 33%;
                    box-sizing: border-box;
                }
                th {
                    background-color: #f2f2f2;
                    font-weight: bold;
                }
                ul, ol {
                    margin: 5px 0;
                    padding-left: 20px;
                }
            </style>
            """
            for delim in delimitadores:
                if delim in linha:
                    partes = linha.split(delim)
                    card_html = """
                    <table style="width: 100%; border-collapse: separate; border-spacing: 0; box-shadow: 0 4px 8px rgba(0,0,0,0.1); border-radius: 8px; margin-bottom: 20px;">
                    """
                    # Se nenhum mapeamento for definido, usar ordem padrão
                    if not self.field_mappings:
                        for i, parte in enumerate(partes):
                            if i < len(campos):
                                campo_nome = campos[i]
                                campo_formatado = parte.strip()
                                for tag, type_ in [('<img', 'img'), ('<source', 'source'), ('<video', 'video')]:
                                    if tag in campo_formatado:
                                        campo_formatado = re.sub(rf'{tag} src="([^"]+)"', lambda m: replace_media_src(m, type_), campo_formatado)
                                if campo_nome in self.field_images and card_index < len(self.field_images[campo_nome]):
                                    campo_formatado += f'<br><img src="{self.field_images[campo_nome][card_index]}">'
                                card_html += f"""
                                <tr><td style="background-color: #444; color: white; padding: 12px; text-align: center; font-weight: bold; font-size: 16px; border-top-left-radius: 8px; border-top-right-radius: 8px;">{campo_nome}</td></tr>
                                <tr><td style="padding: 15px; border: 1px solid #ddd; background-color: white; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;">{campo_formatado}</td></tr>
                                """
                    else:
                        for i, parte in enumerate(partes):
                            campo_nome = self.field_mappings.get(str(i), "Ignorado")
                            if campo_nome != "Ignorado":
                                campo_formatado = parte.strip()
                                for tag, type_ in [('<img', 'img'), ('<source', 'source'), ('<video', 'video')]:
                                    if tag in campo_formatado:
                                        campo_formatado = re.sub(rf'{tag} src="([^"]+)"', lambda m: replace_media_src(m, type_), campo_formatado)
                                if campo_nome in self.field_images and card_index < len(self.field_images[campo_nome]):
                                    campo_formatado += f'<br><img src="{self.field_images[campo_nome][card_index]}">'
                                card_html += f"""
                                <tr><td style="background-color: #444; color: white; padding: 12px; text-align: center; font-weight: bold; font-size: 16px; border-top-left-radius: 8px; border-top-right-radius: 8px;">{campo_nome}</td></tr>
                                <tr><td style="padding: 15px; border: 1px solid #ddd; background-color: white; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;">{campo_formatado}</td></tr>
                                """
                    card_html += "</table>"

                    if tags_for_current_card:
                        if self.chk_num_tags.isChecked():
                            tags_str = ', '.join(f"{tag}{card_index + 1}" for tag in tags_for_current_card)
                        else:
                            tags_str = ', '.join(tags_for_current_card)
                        card_html += f"<p><b>Tags:</b> {tags_str}</p>"

                    cards_html += card_html
                    break

            cards_html += "</body></html>"
            self.preview_widget.setHtml(cards_html)
        except Exception as e:
            logging.error(f"Erro no update_preview: {str(e)}")
            showWarning(f"Erro na pré-visualização: {str(e)}")

    def apply_text_color(self, color):
        cursor = self.txt_entrada.textCursor()
        if cursor.hasSelection():
            texto = cursor.selectedText()
            cursor.insertText(f'<span style="color:{color}">{texto}</span>')
        else:
            cursor.insertText(f'<span style="color:{color}"></span>')
            cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, 7)
            self.txt_entrada.setTextCursor(cursor)
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def apply_background_color(self, color):
        cursor = self.txt_entrada.textCursor()
        if cursor.hasSelection():
            texto = cursor.selectedText()
            cursor.insertText(f'<span style="background-color:{color}">{texto}</span>')
        else:
            cursor.insertText(f'<span style="background-color:{color}"></span>')
            cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, 7)
            self.txt_entrada.setTextCursor(cursor)
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def clear_all(self):
        reply = QMessageBox.question(self, "Confirmação", "Tem certeza de que deseja limpar tudo? Isso não pode ser desfeito.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.txt_entrada.clear()
            self.txt_tags.clear()
            self.search_input.clear()
            self.replace_input.clear()
            self.deck_name_input.clear()
            self.decks_search_input.clear()
            self.notetypes_search_input.clear()

            for chk in self.chk_delimitadores.values():
                chk.setChecked(False)
            self.chk_num_tags.setChecked(False)
            self.chk_repetir_tags.setChecked(False)
            self.cloze_2_count = 1
            self.zoom_factor = 1.0
            self.txt_entrada.zoomOut(int((self.zoom_factor - 1.0) * 10))
            self.initial_tags_set = False
            self.initial_numbering_set = False
            self.current_line = 0
            self.previous_text = ""
            self.last_edited_line = -1
            self.last_search_query = ""
            self.last_search_position = 0
            self.field_mappings.clear()
            self.field_images.clear()

            self.media_files.clear()

            self.update_field_mappings()
            self.update_preview()
            self.schedule_save()  # Salvar o estado limpo
            showInfo("Todos os campos e configurações foram limpos!")

    def add_cards(self):
        deck = self.lista_decks.currentItem()
        notetype = self.lista_notetypes.currentItem()
        if not deck or not notetype:
            showWarning("Selecione um deck e um modelo!")
            return
        delimitadores = [chk.simbolo for chk in self.chk_delimitadores.values() if chk.isChecked()]
        if not delimitadores:
            showWarning("Selecione pelo menos um delimitador!")
            return
        linhas = self.txt_entrada.toPlainText().strip().split('\n')
        if not linhas:
            showWarning("Digite algum conteúdo!")
            return
        modelo = mw.col.models.by_name(notetype.text())
        campos = [fld['name'] for fld in modelo['flds']]
        contador = 0

        linhas_tags = self.txt_tags.toPlainText().strip().split('\n')

        card_index = 0

        for i, linha in enumerate(linhas):
            if not linha.strip():
                continue

            for delim in delimitadores:
                if delim in linha:
                    partes = linha.split(delim)
                    nota = mw.col.new_note(modelo)
                    # Se nenhum mapeamento for definido, usar ordem padrão
                    if not self.field_mappings:
                        for idx, parte in enumerate(partes):
                            if idx < len(campos):
                                campo_nome = campos[idx]
                                campo_idx = next(i for i, f in enumerate(modelo['flds']) if f['name'] == campo_nome)
                                conteudo = parte.strip()
                                if campo_nome in self.field_images and card_index < len(self.field_images[campo_nome]):
                                    conteudo += f'<br><img src="{self.field_images[campo_nome][card_index]}">'
                                nota.fields[campo_idx] = conteudo
                    else:
                        for idx, parte in enumerate(partes):
                            if str(idx) in self.field_mappings:
                                campo_nome = self.field_mappings[str(idx)]
                                campo_idx = next(i for i, f in enumerate(modelo['flds']) if f['name'] == campo_nome)
                                conteudo = parte.strip()
                                if campo_nome in self.field_images and card_index < len(self.field_images[campo_nome]):
                                    conteudo += f'<br><img src="{self.field_images[campo_nome][card_index]}">'
                                nota.fields[campo_idx] = conteudo

                    tags_for_card = []
                    if i < len(linhas_tags):
                        tags_for_card = [tag.strip() for tag in linhas_tags[i].split(',') if tag.strip()]
                    if tags_for_card:
                        if self.chk_num_tags.isChecked():
                            nota.tags.extend([f"{tag}{card_index + 1}" for tag in tags_for_card])
                        else:
                            nota.tags.extend(tags_for_card)

                    try:
                        mw.col.add_note(nota, mw.col.decks.by_name(deck.text())['id'])
                        contador += 1
                        card_index += 1
                    except Exception as e:
                        print(f"Erro ao adicionar card: {str(e)}")
                    break

        showInfo(f"{contador} cards adicionados com sucesso!")

    def add_image(self):
        arquivos, _ = QFileDialog.getOpenFileNames(self, "Selecionar Arquivos", "", "Mídia (*.png *.jpg *.jpeg *.gif *.mp3 *.wav *.ogg *.mp4 *.webm)")
        if arquivos:
            media_dir = mw.col.media.dir()
            for caminho in arquivos:
                nome = os.path.basename(caminho)
                destino = os.path.join(media_dir, nome)
                if not os.path.exists(destino):
                    shutil.copy(caminho, destino)
                self.media_files.append(nome)
                ext = os.path.splitext(nome)[1].lower()
                if ext in ('.png', '.jpg', '.jpeg', '.gif'):
                    self.txt_entrada.insertPlainText(f'<img src="{nome}">\n')
                elif ext in ('.mp3', '.wav', '.ogg'):
                    self.txt_entrada.insertPlainText(f'<audio controls=""><source src="{nome}" type="audio/mpeg"></audio>\n')
                elif ext in ('.mp4', '.webm'):
                    self.txt_entrada.insertPlainText(f'<video src="{nome}" controls width="320" height="240"></video>\n')
            self.previous_text = self.txt_entrada.toPlainText()
            self.update_preview()

    def drag_enter_event(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def drop_event(self, event):
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            file_paths = [url.toLocalFile() for url in mime_data.urls()]
            self.process_files(file_paths)
            event.acceptProposedAction()
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def process_files(self, file_paths):
        media_folder = mw.col.media.dir()
        for file_path in file_paths:
            file_name = os.path.basename(file_path)
            new_path = os.path.join(media_folder, file_name)
            if os.path.exists(new_path):
                base_name, ext = os.path.splitext(file_name)
                counter = 1
                while os.path.exists(new_path):
                    file_name = f"{base_name}{counter}{ext}"
                    new_path = os.path.join(media_folder, file_name)
                    counter += 1
            shutil.copy(file_path, new_path)
            self.media_files.append(file_name)
            ext = file_name.lower()
            if ext.endswith(('.png', '.xpm', '.jpg', '.jpeg', '.bmp', '.gif')):
                self.txt_entrada.insertPlainText(f'<img src="{file_name}">\n')
            elif ext.endswith(('.mp3', '.wav', '.ogg')):
                self.txt_entrada.insertPlainText(f'<audio controls=""><source src="{file_name}" type="audio/mpeg"></audio>\n')
            elif ext.endswith(('.mp4', '.webm', '.avi', '.mkv', '.mov')):
                self.txt_entrada.insertPlainText(f'<video src="{file_name}" controls width="320" height="240"></video>\n')

    def show_context_menu(self, pos):
        menu = self.txt_entrada.createStandardContextMenu()
        paste_action = QAction("Colar HTML sem Tag e sem Formatação", self)
        paste_action.triggered.connect(self.paste_html)
        menu.addAction(paste_action)

        paste_raw_action = QAction("Colar com Tags HTML", self)
        paste_raw_action.triggered.connect(self.paste_raw_html)
        menu.addAction(paste_raw_action)

        paste_excel_action = QAction("Colar do Excel com Ponto e Vírgula", self)
        paste_excel_action.triggered.connect(self.paste_excel)
        menu.addAction(paste_excel_action)

        paste_word_action = QAction("Colar do Word", self)
        paste_word_action.triggered.connect(self.paste_word)
        menu.addAction(paste_word_action)

        paste_pdf_action = QAction("Colar do PDF", self)
        paste_pdf_action.triggered.connect(self.paste_pdf)
        menu.addAction(paste_pdf_action)

        menu.exec(self.txt_entrada.mapToGlobal(pos))

    def convert_markdown_to_html(self, text):
        lines = text.split('\n')
        table_html = ""
        in_table = False
        headers = []
        rows = []
        table_start_idx = -1

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            if line.startswith('|') and line.endswith('|') and '|' in line[1:-1]:
                cells = [cell.strip() for cell in line[1:-1].split('|')]
                if not in_table and i + 1 < len(lines) and re.match(r'^\|(?:\s*[-:]+(?:\s*\|)?)+$', lines[i + 1]):
                    in_table = True
                    table_start_idx = i
                    headers = cells
                    continue
                elif in_table:
                    rows.append(cells)
            elif in_table:
                if headers and rows:
                    table_html += "<table>\n<thead>\n<tr>"
                    for header in headers:
                        table_html += f"<th>{header}</th>"
                    table_html += "</tr>\n</thead>\n<tbody>\n"
                    for row in rows:
                        while len(row) < len(headers):
                            row.append("")
                        table_html += "<tr>"
                        for cell in row[:len(headers)]:
                            table_html += f"<td>{cell}</td>"
                        table_html += "</tr>\n"
                    table_html += "</tbody>\n</table>"
                in_table = False
                headers = []
                rows = []

        if in_table and headers and rows:
            table_html += "<table>\n<thead>\n<tr>"
            for header in headers:
                table_html += f"<th>{header}</th>"
            table_html += "</tr>\n</thead>\n<tbody>\n"
            for row in rows:
                while len(row) < len(headers):
                    row.append("")
                table_html += "<tr>"
                for cell in row[:len(headers)]:
                    table_html += f"<td>{cell}</td>"
                table_html += "</tr>\n"
            table_html += "</tbody>\n</table>"

        if table_html:
            new_lines = []
            in_table = False
            for i, line in enumerate(lines):
                if i == table_start_idx:
                    in_table = True
                    continue
                elif in_table and (line.strip().startswith('|') and line.strip().endswith('|') and '|' in line.strip()[1:-1] or re.match(r'^\|(?:\s*[-:]+(?:\s*\|)?)+$', line)):
                    continue
                else:
                    in_table = False
                    if line.strip():
                        new_lines.append(line.rstrip())

            remaining_text = '\n'.join(new_lines).rstrip()
            if remaining_text:
                text = remaining_text + '\n' + table_html.rstrip()
            else:
                text = table_html.rstrip()
        else:
            text = '\n'.join(line.rstrip() for line in lines if line.strip()).rstrip()
        return text

    def paste_html(self):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        if mime_data.hasHtml():
            html = mime_data.html()
            cleaned_text = strip_html(html)
            cleaned_text = self.convert_markdown_to_html(cleaned_text)
            self.txt_entrada.insertPlainText(cleaned_text)
        elif mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull():
                media_folder = mw.col.media.dir()
                base_name, ext, counter = "img", ".png", 1
                file_name = f"{base_name}{counter}{ext}"
                new_path = os.path.join(media_folder, file_name)
                while os.path.exists(new_path):
                    counter += 1
                    file_name = f"{base_name}{counter}{ext}"
                    new_path = os.path.join(media_folder, file_name)
                image.save(new_path)
                self.media_files.append(file_name)
                self.txt_entrada.insertPlainText(f'<img src="{file_name}">\n')
        elif mime_data.hasText():
            text = clipboard.text()
            text = self.convert_markdown_to_html(text)
            self.txt_entrada.insertPlainText(text)
        else:
            showWarning("Nenhuma imagem, texto ou HTML encontrado na área de transferência.")
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def paste_excel(self):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        if mime_data.hasText():
            text = clipboard.text()
            lines = text.strip().split('\n')
            formatted_lines = []
            for line in lines:
                columns = line.split('\t')
                columns = [col.strip() for col in columns]
                formatted_line = ' ; '.join(columns)
                formatted_lines.append(formatted_line)
            formatted_text = '\n'.join(formatted_lines)
            self.txt_entrada.insertPlainText(formatted_text)
            self.previous_text = self.txt_entrada.toPlainText()
            self.update_preview()
        else:
            showWarning("Nenhum texto encontrado na área de transferência para colar como Excel.")

    def paste_word(self):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        if mime_data.hasHtml():
            html = mime_data.html()
            fragment_match = re.search(r'<!--StartFragment-->(.*?)<!--EndFragment-->', html, re.DOTALL)
            if fragment_match:
                html = fragment_match.group(1)

            def clean_style_attr(match):
                style_content = match.group(1)
                style_content = re.sub(r'mso-highlight:([\w-]+)', r'background-color:\1', style_content, flags=re.IGNORECASE)
                cleaned_style = re.sub(r'mso-[^;:]*:[^;]*;?', '', style_content)
                cleaned_style = re.sub(r'background:([^;]*)', r'background-color:\1', cleaned_style)
                styles = cleaned_style.split(';')
                style_dict = {}
                for style in styles:
                    if style.strip():
                        key, value = style.split(':')
                        style_dict[key.strip()] = value.strip()
                cleaned_style = ', '.join(f'{key}:{value}' for key, value in style_dict.items() if key in ['color', 'background-color'])
                return f"style='{cleaned_style}'" if cleaned_style else ''

            html = re.sub(r"style=['\"]([^'\"]*)['\"]", clean_style_attr, html)

            def preserve_colored_spans(match):
                full_span = match.group(0)
                content = match.group(1)
                style = ''
                color_match = re.search(r'color:([#\w]+)', full_span, re.IGNORECASE)
                if color_match and color_match.group(1).lower() != '#000000':
                    style += f'color:{color_match.group(1)}'
                bg_match = re.search(r'background-color:([#\w]+)', full_span, re.IGNORECASE)
                if bg_match and bg_match.group(1).lower() != 'transparent':
                    if style:
                        style += ', '
                    style += f'background-color:{bg_match.group(1)}'
                if style:
                    return f'<span style="{style}">{content}</span>'
                return content

            previous_html = None
            while html != previous_html:
                previous_html = html
                html = re.sub(r'<span[^>]*>(.*?)</span>', preserve_colored_spans, html, flags=re.DOTALL)

            html = html.replace(';', ',')
            html = re.sub(r'\s+', ' ', html).strip()

            self.txt_entrada.insertPlainText(html)
            self.previous_text = self.txt_entrada.toPlainText()
            self.update_preview()
        elif mime_data.hasText():
            text = clipboard.text()
            lines = text.strip().split('\n')
            lines = [line.strip() for line in lines if line.strip()]
            formatted_text = ' '.join(lines)
            self.txt_entrada.insertPlainText(formatted_text)
            self.previous_text = self.txt_entrada.toPlainText()
            self.update_preview()
        else:
            showWarning("Nenhum texto encontrado na área de transferência para colar como Word.")

    def paste_pdf(self):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        formats = mime_data.formats()
        print(f"Formatos disponíveis no clipboard: {formats}")

        if mime_data.hasHtml():
            html = mime_data.html()
            body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
            if body_match:
                body_content = body_match.group(1)
            else:
                body_content = html

            processed_html = re.sub(r'<p[^>]*style=["\']([^"\']*)["\'][^>]*>(.*?)</p>',
                                   r'<span style="\1">\2</span><br>', body_content, flags=re.DOTALL|re.IGNORECASE)

            styles = []
            style_matches = re.finditer(r'style=["\']([^"\']*)["\']', processed_html)
            for match in style_matches:
                style_text = match.group(1)
                font_family = re.search(r'font-family:\'?([^;\']+)\'?', style_text)
                font_size = re.search(r'font-size:([^;]+)', style_text)
                color = re.search(r'color:([^;]+)', style_text)

                style_dict = {}
                if font_family:
                    style_dict['font-family'] = font_family.group(1).strip()
                if font_size:
                    style_dict['font-size'] = font_size.group(1).strip()
                if color:
                    style_dict['color'] = color.group(1).strip()

                if style_dict:
                    styles.append(style_dict)

            bold_elements = []
            bold_by_weight = re.finditer(r'<[^>]*font-weight:\s*[6-9]\d\d|bold[^>]*>(.*?)</[^>]*>', processed_html, re.DOTALL)
            for match in bold_by_weight:
                bold_elements.append(match.group(1))
            bold_by_name = re.finditer(r'<[^>]*font-family:[^>]*Bold[^>]*>(.*?)</[^>]*>', processed_html, re.DOTALL | re.IGNORECASE)
            for match in bold_by_name:
                bold_elements.append(match.group(1))

            result_html = ""
            paragraphs = re.split(r'<p[^>]*>|</p>|<br\s*/?>|<div[^>]*>|</div>', processed_html)
            paragraphs = [p.strip() for p in paragraphs if p.strip()]

            for p in paragraphs:
                clean_text = re.sub(r'<[^>]*>', '', p).strip()
                if not clean_text:
                    continue

                is_heading = any(re.match(r'^\d+(\.\d+)*\s', clean_text) for _ in range(1))
                is_bold = any(clean_text in bold for bold in bold_elements)

                style_to_apply = {}
                for style in styles:
                    if 'Bold' in style.get('font-family', ''):
                        if is_heading or is_bold:
                            style_to_apply = style
                            break
                    elif style.get('font-size', '').startswith('1'):
                        style_to_apply = style

                style_str = ""
                if style_to_apply:
                    style_parts = []
                    for k, v in style_to_apply.items():
                        style_parts.append(f"{k}:{v}")
                    style_str = ", ".join(style_parts)

                formatted_text = clean_text
                urls = re.finditer(r'(https?://[^\s\)\]]+)', clean_text)
                for url_match in urls:
                    url = url_match.group(1)
                    url = re.sub(r'([,;:\.\)])+$', '', url)
                    formatted_text = formatted_text.replace(url,
                        f'</span><a href="{url}" style="font-size:15.0pt, font-family:LiberationSerif, color:blue">{url}</a><span style="{style_str}">')

                italic_terms = ['Play Store', 'App Store']
                for term in italic_terms:
                    if term in formatted_text:
                        formatted_text = formatted_text.replace(term, f'</span><i><span style="font-size:15.0pt, font-family: LiberationSerif-Italic, color:black ">{term}</span></i><span style="{style_str}">')

                if is_heading:
                    result_html += f'<b><span style="font-size:17.0pt, font-family:LiberationSerif-Bold, color:black">{formatted_text}</span></b><br>'
                else:
                    result_html += f'<span style="{style_str}">{formatted_text}</span><br>'

            self.txt_entrada.setPlainText(result_html)
            self.previous_text = self.txt_entrada.toPlainText()
            self.update_preview()
            return

        if "application/rtf" in formats or "text/rtf" in formats:
            rtf_data = mime_data.data("application/rtf") or mime_data.data("text/rtf")
            if rtf_data:
                try:
                    html = self.convert_rtf_to_html(rtf_data)
                    if html:
                        self.txt_entrada.setPlainText(html)
                        self.previous_text = self.txt_entrada.toPlainText()
                        self.update_preview()
                        return
                except Exception as e:
                    print(f"Erro ao processar RTF: {str(e)}")

        if mime_data.hasText():
            text = clipboard.text()
            lines = text.strip().split('\n')
            formatted_lines = []

            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue

                is_heading = bool(re.match(r'^\d+(\.\d+)*\s', line))

                formatted_line = line
                urls = re.finditer(r'(https?://[^\s\)\]]+)', line)
                has_link = False

                for url_match in urls:
                    has_link = True
                    url = url_match.group(1)
                    url = re.sub(r'([,;:\.\)])+$', '', url)
                    formatted_line = formatted_line.replace(url,
                        f'</span><a href="{url}" style="font-size:15.0pt, font-family:LiberationSerif, color:blue">{url}</a><span style="font-size:15.0pt, font-family:LiberationSerif, color:black">')

                if is_heading:
                    formatted_lines.append(f'<b><span style="font-size:17.0pt, font-family:LiberationSerif-Bold, color:black">{formatted_line}</span></b>')
                else:
                    line_with_italic = formatted_line
                    italic_terms = ['Play Store', 'App Store']
                    for term in italic_terms:
                        if term in line_with_italic:
                            line_with_italic = line_with_italic.replace(term,
                                f'</span><i><span style="font-size:15.0pt, font-family: LiberationSerif-Italic, color:black ">{term}</span></i><span style="font-size:15.0pt, font-family:LiberationSerif, color:black ">')

                    formatted_lines.append(f'<span style="font-size:15.0pt, font-family:LiberationSerif, color:black">{line_with_italic}</span>')

            formatted_text = '<br>'.join(formatted_lines)
            self.txt_entrada.setPlainText(formatted_text)
            self.previous_text = self.txt_entrada.toPlainText()
            self.update_preview()
            return

        QMessageBox.warning(self, "Aviso", "Nenhum texto com formatação identificável encontrado na área de transferência.")

    def convert_rtf_to_html(self, rtf_data):
        try:
            rtf_str = rtf_data.data().decode('utf-8', errors='ignore')
            html = rtf_str

            html = re.sub(r'\\b\s+(.*?)\\b0\s+', r'<b><span style="font-size:15.0pt, font-family:LiberationSerif-Bold, color:black">\1</span></b>', html)
            html = re.sub(r'\\i\s+(.*?)\\i0\s+', r'<i><span style="font-size:15.0pt, font-family:LiberationSerif-Italic, color:black">\1</span></i>', html)

            urls = re.finditer(r'(https?://[^\s\\\{\}\[\]\)\]]+)', html)
            for url_match in urls:
                actual_url = url_match.group(1)
                actual_url = re.sub(r'([,;:\.\)])+$', '', actual_url)
                html = html.replace(actual_url,
                      f'<a href="{actual_url}" style="font-size:15.0pt, font-family:LiberationSerif, color:blue">{actual_url}</a>')

            html = re.sub(r'\\pard\s+(.*?)(?:\\par|$)',
                         r'<span style="font-size:15.0pt, font-family:LiberationSerif, color:black">\1</span><br>',
                         html)

            headers = re.finditer(r'(^\d+(\.\d+)*\s+[^\\\{\}\[\]]+)', html, re.MULTILINE)
            for header in headers:
                header_text = header.group(1)
                html = html.replace(header_text,
                                  f'<b><span style="font-size:17.0pt, font-family:LiberationSerif-Bold, color:black">{header_text}</span></b>')

            html = re.sub(r'\\[a-z]+\d*', '', html)
            html = re.sub(r'[\\{}\[\]]', '', html)
            html = re.sub(r'</span><span', r'</span><br><span', html)

            return html
        except Exception as e:
            print(f"Erro na conversão RTF para HTML: {str(e)}")
            return None

    def paste_raw_html(self):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        if mime_data.hasHtml():
            html = mime_data.html()
            tags_to_remove = [
                'html', 'body', 'head', 'meta', 'link', 'script', 'style',
                'title', 'doctype', '!DOCTYPE', 'br', 'hr', 'div', 'p', 'form', 'input', 'button', 'a'
            ]
            pattern = r'</?(?:' + '|'.join(tags_to_remove) + r')(?:\s+[^>])?>'
            cleaned_html = re.sub(pattern, '', html, flags=re.IGNORECASE)
            cleaned_html = self.convert_markdown_to_html(cleaned_html)
            def protect_structures(match):
                return match.group(0).replace('\n', ' PROTECTED_NEWLINE ')
            cleaned_html = re.sub(r'<ul>.?</ul>|<ol>.?</ol>|<li>.?</li>|<table>.?</table>', protect_structures, cleaned_html, flags=re.DOTALL)
            lines = cleaned_html.split('\n')
            cleaned_lines = []
            for line in lines:
                line = line.strip()
                if line:
                    cleaned_lines.append(line)
            cleaned_html = '\n'.join(cleaned_lines)
            cleaned_html = cleaned_html.replace(' PROTECTED_NEWLINE ', '\n')
            cleaned_html = re.sub(r'\s+(?![^<]>)', ' ', cleaned_html).strip()
            self.txt_entrada.insertPlainText(cleaned_html)
        elif mime_data.hasText():
            text = clipboard.text()
            text = self.convert_markdown_to_html(text)
            self.txt_entrada.insertPlainText(text)
        else:
            showWarning("Nenhum texto ou HTML encontrado na área de transferência.")
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def eventFilter(self, obj, event):
        if obj == self.txt_entrada:
            if event.type() == QEvent.Type.KeyPress and event.matches(QKeySequence.StandardKey.Paste):
                self.paste_html()
                return True
            elif event.type() == QEvent.Type.FocusOut:
                self.focus_out_event(event)
                return True
            elif event.type() == QEvent.Type.DragEnter:
                self.drag_enter_event(event)
                return True
            elif event.type() == QEvent.Type.Drop:
                self.drop_event(event)
                return True
        return super().eventFilter(obj, event)

    def criar_lista_rolavel(self, itens, altura_min=100):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(altura_min)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lista = QListWidget()
        lista.addItems(itens)
        scroll.setWidget(lista)
        return scroll, lista

    def toggle_group(self):
        novo_estado = not self.group_widget.isVisible()
        self.group_widget.setVisible(novo_estado)
        self.btn_toggle.setText("Ocultar Decks/Modelos/Delimitadores" if novo_estado else "Mostrar Decks/Modelos/Delimitadores")

    def ajustar_tamanho_scroll(self):
        self.scroll_decks.widget().adjustSize()
        self.scroll_notetypes.widget().adjustSize()
        self.scroll_decks.updateGeometry()
        self.scroll_notetypes.updateGeometry()

    def scan_media_files_from_text(self):
        patterns = [
            r'<img src="([^"]+)"',
            r'<source src="([^"]+)"',
            r'<video src="([^"]+)"'
        ]

        current_text = self.txt_entrada.toPlainText()
        media_dir = mw.col.media.dir()
        found_media = set()

        for pattern in patterns:
            matches = re.findall(pattern, current_text)
            for file_name in matches:
                file_path = os.path.join(media_dir, file_name)
                if os.path.exists(file_path) and file_name not in self.media_files:
                    found_media.add(file_name)

        self.media_files.extend(found_media)
        self.media_files = list(dict.fromkeys(self.media_files))



    def toggle_theme(self):
        """Alterna entre tema claro e escuro."""
        self.is_dark_theme = not self.is_dark_theme
        
        if self.is_dark_theme:
            # Aplicar tema escuro
            self.setStyleSheet("""
                QWidget {
                    background-color: #333;
                    color: #eee;
                }
                QTextEdit, QLineEdit, QListWidget {
                    background-color: #444;
                    color: #fff;
                    border: 1px solid #555;
                }
                QPushButton {
                    background-color: #555;
                    color: #fff;
                    border: 1px solid #666;
                    padding: 5px;
                }
                QPushButton:hover {
                    background-color: #666;
                }
                QGroupBox {
                    border: 1px solid #666;
                    margin-top: 10px;
                    padding-top: 15px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 10px;
                    padding: 0 3px;
                }
            """)
        else:
            # Aplicar tema claro (reset)
            self.setStyleSheet("")
        
        self.update_preview()







    def copy_media_files(self, dest_folder):
        """Copia todos os arquivos de mídia usados para a pasta de destino."""
        media_files = set()
        text = self.txt_entrada.toPlainText()
        
        # Encontra todos os arquivos de mídia referenciados
        for pattern in [r'src="([^"]+)"', r'<source src="([^"]+)"', r'<video src="([^"]+)"']:
            media_files.update(re.findall(pattern, text))
        
        # Copia cada arquivo
        media_dir = mw.col.media.dir()
        for file_name in media_files:
            src = os.path.join(media_dir, file_name)
            dst = os.path.join(dest_folder, file_name)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
    



    
    def generate_export_html(self):
        cards = self.txt_entrada.toPlainText().strip().split('\n')
        tags = self.txt_tags.toPlainText().strip().split('\n')
        
        def embed_media(content):
            media_dir = mw.col.media.dir()
            
            def replace_with_data_url(match):
                full_tag = match.group(0)
                file_name = match.group(1)
                file_path = os.path.join(media_dir, file_name)
                
                if not os.path.exists(file_path):
                    return full_tag
                    
                mime_type = {
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.png': 'image/png',
                    '.gif': 'image/gif',
                    '.mp3': 'audio/mpeg',
                    '.wav': 'audio/wav',
                    '.ogg': 'audio/ogg',
                    '.mp4': 'video/mp4',
                    '.webm': 'video/webm'
                }.get(os.path.splitext(file_name)[1].lower(), 'application/octet-stream')
                
                with open(file_path, 'rb') as f:
                    data = base64.b64encode(f.read()).decode('utf-8')
                
                if file_name.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                    return f'<div class="media-container"><img src="data:{mime_type};base64,{data}"></div>'
                
                elif file_name.lower().endswith(('.mp3', '.wav', '.ogg')):
                    return f'<div class="media-container"><audio controls><source src="data:{mime_type};base64,{data}" type="{mime_type}"></audio></div>'
                
                elif file_name.lower().endswith(('.mp4', '.webm')):
                    return f'<div class="media-container"><video controls><source src="data:{mime_type};base64,{data}" type="{mime_type}"></video></div>'
                
                return full_tag
            
            content = re.sub(r'<img\s+src="([^"]+)"\s*>', replace_with_data_url, content)
            content = re.sub(r'<audio\s+.*?<source\s+src="([^"]+)".*?</audio>', replace_with_data_url, content, flags=re.DOTALL)
            content = re.sub(r'<video\s+src="([^"]+)"[^>]*>', replace_with_data_url, content)
            
            return content
    
        html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Cards Exportados</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            .card { border: 1px solid #ddd; padding: 15px; margin-bottom: 20px; border-radius: 5px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
            .card-header { font-weight: bold; margin-bottom: 10px; color: #333; }
            .tags { color: #666; font-size: 0.9em; margin-top: 10px; }
            .fields { display: flex; flex-wrap: wrap; gap: 15px; }
            .field { flex: 1; min-width: 200px; }
            .field-name { font-weight: bold; margin-bottom: 5px; color: #444; }
            .media-container { margin-top: 10px; border: 1px solid #eee; padding: 5px; border-radius: 4px; background: #f9f9f9; }
            .media-container img { max-width: 100%; height: auto; display: block; }
            .media-container audio { width: 100%; }
            .media-container video { max-width: 100%; height: auto; background: #000; }
        </style>
    </head>
    <body>
        <h1>Cards Exportados</h1>
        {cards_content}
    </body>
    </html>
        """
    
        cards_html = []
        
        for i, card in enumerate(cards):
            if not card.strip():
                continue
            
            card_tags = ""
            if i < len(tags) and tags[i].strip():
                card_tags = f'<div class="tags">Tags: {tags[i]}</div>'
            
            fields_html = []
            partes = card.split(';')
            for j, parte in enumerate(partes):
                field_name = f"Campo {j+1}"
                if str(j) in self.field_mappings:
                    field_name = self.field_mappings[str(j)]
                
                field_content = embed_media(parte.strip())
                
                fields_html.append(f"""
                    <div class="field">
                        <div class="field-name">{field_name}</div>
                        <div class="field-content">{field_content}</div>
                    </div>
                """)
            
            cards_html.append(f"""
                <div class="card">
                    <div class="card-header">Card {i+1}</div>
                    <div class="fields">
                        {''.join(fields_html)}
                    </div>
                    {card_tags}
                </div>
            """)
        
        return html_template.replace("{cards_content}", "".join(cards_html))


    
    
    def export_to_html(self):
        """Exporta cards para HTML com mídias incorporadas."""
        try:
            html_content = self.generate_export_html()
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Exportar para HTML", "", "HTML Files (*.html)")
            
            if file_path:
                if not file_path.endswith('.html'):
                    file_path += '.html'
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                showInfo(f"HTML exportado com sucesso!\n{file_path}")
        except Exception as e:
            showWarning(f"Erro ao exportar HTML: {str(e)}")
















    def update_tag_numbers(self):
        linhas_tags = self.txt_tags.toPlainText().strip().split('\n')
        num_linhas_cards = len(self.txt_entrada.toPlainText().strip().splitlines())

        if not any(linhas_tags) and num_linhas_cards > 0:
            self.txt_tags.setPlainText('\n'.join(f"{i + 1}" for i in range(num_linhas_cards)))
            self.initial_numbering_set = True
            self.update_preview()
            return

        if self.chk_num_tags.isChecked() and not self.initial_numbering_set:
            updated_tags = []
            for i in range(num_linhas_cards):
                if i < len(linhas_tags) and linhas_tags[i].strip():
                    tags_for_card = [tag.rstrip('0123456789') for tag in linhas_tags[i].split(',') if tag.strip()]
                    numbered_tags = [f"{tag}{i + 1}" for tag in tags_for_card]
                    updated_tags.append(", ".join(numbered_tags))
                else:
                    updated_tags.append("")
            self.txt_tags.setPlainText('\n'.join(updated_tags))
            self.initial_numbering_set = True
        elif not self.chk_num_tags.isChecked():
            updated_tags = []
            for i in range(num_linhas_cards):
                if i < len(linhas_tags) and linhas_tags[i].strip():
                    tags_for_card = [tag.rstrip('0123456789') for tag in linhas_tags[i].split(',') if tag.strip()]
                    updated_tags.append(", ".join(tags_for_card))
                else:
                    updated_tags.append("")
            self.txt_tags.setPlainText('\n'.join(updated_tags))
            self.initial_numbering_set = False

        self.update_preview()

    def update_repeated_tags(self):
        if self.chk_repetir_tags.isChecked() and not self.initial_tags_set:
            linhas_tags = self.txt_tags.toPlainText().strip().split('\n')
            num_cards = len(self.txt_entrada.toPlainText().strip().splitlines())

            if not any(linhas_tags):
                self.txt_tags.setPlainText('\n' * (num_cards - 1))
                self.initial_tags_set = True
                self.update_preview()
                return

            first_non_empty = next((tags for tags in linhas_tags if tags.strip()), None)
            if not first_non_empty:
                self.txt_tags.setPlainText('\n' * (num_cards - 1))
                self.initial_tags_set = True
                self.update_preview()
                return

            tags = list(dict.fromkeys([tag.strip() for tag in first_non_empty.split(',') if tag.strip()]))
            if not tags:
                self.txt_tags.setPlainText('\n' * (num_cards - 1))
                self.initial_tags_set = True
                self.update_preview()
                return

            self.txt_tags.setPlainText('\n'.join([", ".join(tags)] * num_cards))
            self.initial_tags_set = True
        elif not self.chk_repetir_tags.isChecked():
            self.initial_tags_set = False
            self.update_tag_numbers()

        self.update_preview()

    def search_text(self):
        search_query = self.search_input.text().strip()
        if not search_query:
            showWarning("Por favor, insira um texto para pesquisar.")
            return
        search_words = search_query.split()
        if search_query != self.last_search_query:
            self.last_search_query = search_query
            self.last_search_position = 0
        cursor = self.txt_entrada.textCursor()
        cursor.setPosition(self.last_search_position)
        self.txt_entrada.setTextCursor(cursor)
        found = False
        for word in search_words:
            if self.txt_entrada.find(word):
                self.last_search_position = self.txt_entrada.textCursor().position()
                found = True
                break
        if not found:
            self.txt_entrada.moveCursor(QTextCursor.MoveOperation.Start)
            for word in search_words:
                if self.txt_entrada.find(word):
                    self.last_search_position = self.txt_entrada.textCursor().position()
                    found = True
                    break
        if not found:
            showWarning(f"Texto '{search_query}' não encontrado.")
        self.update_preview()

    def replace_text(self):
        search_query = self.search_input.text().strip()
        replace_text = self.replace_input.text().strip()
        if not search_query:
            showWarning("Por favor, insira um texto para pesquisar.")
            return
        full_text = self.txt_entrada.toPlainText()
        replaced_text = re.sub(re.escape(search_query), replace_text, full_text, flags=re.IGNORECASE)
        self.txt_entrada.setPlainText(replaced_text)
        self.previous_text = replaced_text
        self.update_preview()
        showInfo(f"Todas as ocorrências de '{search_query}' foram {'substituídas por ' + replace_text if replace_text else 'removidas'}.")

    def zoom_in(self):
        self.txt_entrada.zoomIn(1)
        self.zoom_factor += 0.1

    def create_deck(self):
        deck_name = self.deck_name_input.text().strip()
        if not deck_name:
            showWarning("Por favor, insira um nome para o deck!")
            return
        try:
            mw.col.decks.id(deck_name)
            self.lista_decks.clear()
            self.lista_decks.addItems([d.name for d in mw.col.decks.all_names_and_ids()])
            self.deck_name_input.clear()
            showInfo(f"Deck '{deck_name}' criado com sucesso!")
            self.schedule_save()  # Salvar após criar deck
        except Exception as e:
            showWarning(f"Erro ao criar o deck: {str(e)}")

    def zoom_out(self):
        if self.zoom_factor > 0.2:
            self.txt_entrada.zoomOut(1)
            self.zoom_factor -= 0.1

    def filter_list(self, list_widget, search_input, full_list):
        search_text = search_input.text().strip().lower()
        filtered = [item for item in full_list if search_text in item.lower()]
        list_widget.clear()
        list_widget.addItems(filtered)
        if filtered and search_text:
            list_widget.setCurrentRow(0)

    def filter_decks(self):
        self.filter_list(self.lista_decks, self.decks_search_input, [d.name for d in mw.col.decks.all_names_and_ids()])

    def filter_notetypes(self):
        self.filter_list(self.lista_notetypes, self.notetypes_search_input, mw.col.models.all_names())

    def create_focus_handler(self, widget, field_type):
        def focus_in_event(event):
            self.txt_entrada.setStyleSheet("")
            self.txt_tags.setStyleSheet("")
            widget.setStyleSheet(f"border: 2px solid {'blue' if field_type == 'cards' else 'green'};")
            self.tags_label.setText("Etiquetas:" if field_type == "cards" else "Etiquetas (Selecionado)")
            if isinstance(widget, QTextEdit):
                QTextEdit.focusInEvent(widget, event)
        return focus_in_event



    def concatenate_text(self):
        clipboard = QApplication.clipboard()
        copied_text = clipboard.text().strip().split("\n")
        current_widget = self.txt_entrada if self.txt_entrada.styleSheet() else self.txt_tags if self.txt_tags.styleSheet() else self.txt_entrada
        current_text = current_widget.toPlainText().strip().split("\n")
        result_lines = [f"{current_text[i] if i < len(current_text) else ''}{copied_text[i] if i < len(copied_text) else ''}".strip() for i in range(max(len(current_text), len(copied_text)))]
        current_widget.setPlainText("\n".join(result_lines))
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def add_cloze_1(self):
        cursor = self.txt_entrada.textCursor()
        selected_text = cursor.selectedText().strip()
        if not selected_text:
            showWarning("Por favor, selecione uma palavra para adicionar o cloze.")
            return
        cursor.insertText(f"{{{{c1::{selected_text}}}}}")
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def add_cloze_2(self):
        cursor = self.txt_entrada.textCursor()
        selected_text = cursor.selectedText().strip()
        if not selected_text:
            showWarning("Por favor, selecione uma palavra para adicionar o cloze.")
            return
        cursor.insertText(f"{{{{c{self.cloze_2_count}::{selected_text}}}}}")
        self.cloze_2_count += 1
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def remove_cloze(self):
        self.txt_entrada.setPlainText(re.sub(r'{{c\d+::(.*?)}}', r'\1', self.txt_entrada.toPlainText()))
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def load_settings(self):
        logging.debug("Carregando configurações do arquivo")
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    dados = json.load(f)
                    conteudo = dados.get('conteudo', '')
                    logging.debug(f"Conteúdo carregado do CONFIG_FILE: '{conteudo}'")
                    self.txt_entrada.setPlainText(conteudo)
                    self.previous_text = self.txt_entrada.toPlainText()
                    self.txt_tags.setPlainText(dados.get('tags', ''))
                    for nome, estado in dados.get('delimitadores', {}).items():
                        if nome in self.chk_delimitadores:
                            self.chk_delimitadores[nome].setChecked(estado)
                    for key, lista in [('deck_selecionado', self.lista_decks), ('modelo_selecionado', self.lista_notetypes)]:
                        if dados.get(key):
                            items = lista.findItems(dados[key], Qt.MatchFlag.MatchExactly)
                            if items:
                                lista.setCurrentItem(items[0])
                    self.field_mappings = dados.get('field_mappings', {})
                    self.update_field_mappings()
                    logging.debug(f"Configurações carregadas: {dados}")
            except Exception as e:
                logging.error(f"Erro ao carregar configurações: {str(e)}")
                showWarning(f"Erro ao carregar configurações: {str(e)}")
        else:
            logging.debug("Arquivo CONFIG_FILE não encontrado")

    def closeEvent(self, event):
        """Lida com o fechamento do diálogo principal."""
        # Limpa a referência na janela principal
        if hasattr(mw, 'delimitadores_dialog'):
            mw.delimitadores_dialog = None
        
        # Limpa referência do diálogo de mídia se existir
        if hasattr(self, 'media_dialog') and self.media_dialog:
            self.media_dialog.close()
            self.media_dialog = None
        
        super().closeEvent(event)


    def join_lines(self):
        texto = self.txt_entrada.toPlainText()
        if '\n' not in texto:
            if hasattr(self, 'original_text'):
                self.txt_entrada.setPlainText(self.original_text)
                del self.original_text
        else:
            self.original_text = texto
            self.txt_entrada.setPlainText(texto.replace('\n', ' '))
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def wrap_selected_text(self, tag):
        cursor = self.txt_entrada.textCursor()
        if cursor.hasSelection():
            texto = cursor.selectedText()
            cursor.insertText(f"{tag[0]}{texto}{tag[1]}")
        else:
            cursor.insertText(f"{tag[0]}{tag[1]}")
            cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, len(tag[1]))
            self.txt_entrada.setTextCursor(cursor)
        self.previous_text = self.txt_entrada.toPlainText()
        self.update_preview()

    def apply_bold(self): self.wrap_selected_text(('<b>', '</b>'))
    def apply_italic(self): self.wrap_selected_text(('<i>', '</i>'))
    def apply_underline(self): self.wrap_selected_text(('<u>', '</u>'))
    def destaque_texto(self): self.wrap_selected_text(('<mark>', '</mark>'))

    def manage_media(self):
        """Abre o diálogo de gerenciamento de mídia com controle de instância única."""
        if hasattr(self, 'media_dialog') and self.media_dialog:
            # Se já existe, traz para frente
            self.media_dialog.showNormal()
            self.media_dialog.raise_()
            self.media_dialog.activateWindow()
            return
        
        self.scan_media_files_from_text()
        
        if not self.media_files:
            showWarning("Nenhum arquivo de mídia foi adicionado ou referenciado no texto!")
            return
        
        # Cria nova instância se não existir
        self.media_dialog = MediaManagerDialog(self, self.media_files, self.txt_entrada, mw)
        self.media_dialog.show()



    def show_dialog():
        global custom_dialog_instance
        
        if not hasattr(mw, 'custom_dialog_instance') or not mw.custom_dialog_instance:
            mw.custom_dialog_instance = CustomDialog(mw)
        
        if mw.custom_dialog_instance.isVisible():
            # Se já está visível, traz para frente
            mw.custom_dialog_instance.raise_()
            mw.custom_dialog_instance.activateWindow()
        else:
            # Se está minimizado ou oculto, mostra normalmente
            mw.custom_dialog_instance.show()


    def closeEvent(self, event):
        """Lida com o fechamento do diálogo."""
        global custom_dialog_instance
        if hasattr(mw, 'custom_dialog_instance'):
            mw.custom_dialog_instance = None
        super().closeEvent(event)



    def view_cards_dialog(self):
        if self.visualizar_dialog is None or not self.visualizar_dialog.isVisible():
            self.visualizar_dialog = VisualizarCards(self)
            self.visualizar_dialog.show()
        else:
            self.visualizar_dialog.raise_()
            self.visualizar_dialog.activateWindow()