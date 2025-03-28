# __init__.py

from aqt import mw
from aqt.qt import QAction
from .dialog import CustomDialog

#def abrir_janela():
  #  dialogo = CustomDialog(parent=mw)
    #dialogo.show()

def abrir_janela():
    # Verifica se já existe uma instância do diálogo
    if hasattr(mw, 'delimitadores_dialog') and mw.delimitadores_dialog:
        # Se existe, traz para frente
        mw.delimitadores_dialog.showNormal()  # Restaura se minimizado
        mw.delimitadores_dialog.raise_()
        mw.delimitadores_dialog.activateWindow()
    else:
        # Se não existe, cria nova instância
        dialogo = CustomDialog(parent=mw)
        dialogo.show()
        # Armazena a referência na janela principal
        mw.delimitadores_dialog = dialogo

# Add the action to the Tools menu in Anki
acao = QAction(" 🙂 Adicionar Cards com Delimitadores", mw)
acao.triggered.connect(abrir_janela)
mw.form.menuTools.addAction(acao)