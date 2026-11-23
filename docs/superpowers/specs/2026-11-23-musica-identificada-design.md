# Tirar só a música com copyright, e pôr música livre por baixo

Data: 2026-11-23 · Status: aprovado pelo dono (2026-11-23, "ok" à combinação)

## Por quê

A checagem de música (`media/music_check.py`) tira **toda** música dos clipes: um classificador acha os
segundos com música e um separador deixa só as vozes nesses trechos. O dono viu um clipe do 3.0 sem a música
do GTA e achou que era música do jogo, sem copyright. Ele quer tirar só o que tem dono e, onde tirar, pôr
uma música livre por baixo. Tudo de graça.

## O que muda

Em `clean_clip`, depois de achar os trechos com música e antes de separar as vozes, cada trecho com 3 s ou
mais é identificado:

1. **ShazamIO** (biblioteca grátis, acesso não oficial ao Shazam) recebe até 12 s do meio do trecho.
   - **Reconheceu**, e o artista, selo ou título bate com a **lista de liberados** → o trecho fica como está.
   - **Reconheceu** e não está na lista → o trecho é tratado como música com copyright (tira).
   - **Respondeu sem reconhecer** (trilha do jogo, som ambiente) → o trecho fica.
2. **ShazamIO falhou** (erro, bloqueio, tempo esgotado): vale a **regra da música cantada**. Tira o trecho só
   se houver canto, medido pela classe "Singing" do mesmo classificador (PANNs).
3. Trechos com menos de 3 s seguem a regra de hoje (tira).

Onde a música for tirada, entra uma **música livre da pasta do dono** por baixo:
- Uma faixa sorteada por clipe, a mesma em todos os trechos daquele clipe, sempre na mesma ordem para o
  mesmo arquivo.
- Volume baixo e fixo, abaixo da voz, com entrada e saída suaves (0,3 s).
- Pasta vazia ou ausente → só tira a música, como hoje.

A "sobra" de hoje (música ainda audível depois de tirar → status `left`, o robô não posta) continua, medida
antes de pôr a música livre.

## Arquivos do dono (fora do git, no disco de dados)

- Pasta de músicas livres: `MUSIC_BED_DIR`, padrão `/app/uploads/kit-assets/music-bed/`
  (`.mp3`, `.wav`, `.m4a`, `.ogg`).
- Lista de liberados: `MUSIC_ALLOWLIST`, padrão `/app/uploads/kit-assets/music-allowlist.txt`. Um nome por
  linha, comparado sem maiúsculas/minúsculas contra artista, selo e título. Se o arquivo não existir, valem
  `NoCopyrightSounds`, `NCS` e `StreamBeats`.

## Fora

- Saber se a música está no Content ID do YouTube: não há API, e o dono escolheu a regra simples.
- Banco próprio das rádios do GTA (Panako): fica para depois, se o ShazamIO falhar muito.
- Escolher a música livre pelo clima do clipe: fica para depois.

## Riscos

- O ShazamIO pode parar de funcionar a qualquer momento: aí vale só a regra da música cantada.
- Uma música comercial instrumental que o Shazam não reconheça fica no clipe.
- "Sem copyright" às vezes tem condições (crédito, só YouTube); a lista de liberados é do dono.

## Para quem constrói

O resultado (`removed`/`kept`/`left`, faixa reconhecida, fonte da decisão) vai para o log e para o retorno de
`clean_clip`, para auditoria. A biblioteca `shazamio` entra nas dependências do backend; sem rede ou sem a
biblioteca, vale a regra da música cantada. Testes com o Shazam simulado.
