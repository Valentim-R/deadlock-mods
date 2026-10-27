# Deadlock Mods

Gerenciador de mods do Deadlock para Linux e Windows, direto do GameBanana.

- Busca, instala e atualiza mods do GameBanana (sem conta, sem chave)
- Ordem de prioridade arrastando a lista; ativa/desativa sem apagar
- Skins por herói com prévia 3D renderizada a partir dos arquivos do jogo
- Ícones do herói (seleção e barra de cima da partida) gerados a partir da skin ativa
- Corrige a câmera de skins antigas (atualização de 16/10/2026) e o `gameinfo.gi` depois de updates do jogo
- Atualiza sozinho pelas Releases deste repositório

## Baixar

Na página de [Releases](../../releases/latest):

- **Linux:** `DeadlockMods-x86_64.AppImage` (precisa de glibc 2.34+ e libfuse2). `chmod +x` e abrir.
- **Windows:** `DeadlockMods-Setup-X.Y.Z.exe` (instala só pro usuário, sem admin). O `.zip` é a versão portátil, que não se atualiza sozinha.

## Rodar do código

```sh
pip install PySide6-Essentials requests numpy backports.zstd
python main.py
```

Precisa do `7z` no PATH pra extrair os mods.

## Gerar uma versão

1. Subir `__version__` em `dlmods/__init__.py`
2. `packaging/release.sh "o que mudou"`: gera o AppImage e o instalador do Windows (via Wine) e publica a release `vX.Y.Z` com os arquivos
