# Seguir canais sem limite; limite só nas lives gravando ao mesmo tempo

## Por quê

O dono segue 20 canais (o máximo) e quer seguir mais. A maioria fica offline a maior parte do tempo, e
canal offline não custa nada. O que custa é gravar: cada live gravando usa ~40% de um núcleo (16 núcleos
na máquina) e disco. O filtro de nota só segura a renderização, não a gravação.

## O que muda

1. **Canais seguidos: sem limite.** Sai a checagem `LIVE_MAX_CHANNELS_PER_USER` ao adicionar canal (e o
   `at_limit` da lista de lives em alta, que deixava o botão "Adicionar" cinza).
2. **Lives gravando ao mesmo tempo: número fixo que o dono escolhe na página de Lives** (escolha do dono:
   opção 1, "número fixo + aviso"). Começa em 10 (o valor de hoje). Guardado por conta.
   - O `.env` mantém um teto da máquina (`LIVE_MAX_RECORDINGS`, sobe para 24); o número da conta não passa
     dele, e a divisão justa entre contas continua como hoje.
   - Quando as vagas enchem, o canal que entra ao vivo fica "aguardando" e começa quando uma vaga libera
     (já é assim).
3. **Painel na página de Lives:** "12 de 15 gravando · 3 aguardando vaga", com o campo do número.
4. **Aviso** no mesmo painel, em amarelo, quando:
   - o processador está apertado: média de 5 minutos acima de 75% dos núcleos;
   - o disco dos dados tem menos de 50 GB livres.
   O aviso só informa; não muda o número sozinho.
5. **Dados no disco Jogos** (231 GB livres): clipes (`/app/uploads`) e gravações (`/data` do robô) passam
   para `/var/mnt/jogos/supoclip/`.
   - Só nesta máquina: num `docker-compose.override.yml` local (fora do git, via `.git/info/exclude`),
     que troca os volumes por pastas do Jogos e desliga o rótulo SELinux nesses containers (testado: sem
     isso o container não consegue gravar no NTFS).
   - Se o disco Jogos não montar no boot (o fstab tem `nofail`), a pasta `supoclip` não existe e os
     containers não sobem, em vez de gravar no disco errado.
   - Mudança: copiar com tudo rodando, parar tudo por alguns minutos para a última cópia, subir de novo
     (as lives voltam para a mesma gravação). Os volumes antigos ficam como cópia de segurança até o dono
     mandar apagar.

## Fora

- Partição do Windows: não mexer (apagar não tem volta; o Jogos basta por agora).
- Escolher sozinho o número pela folga da máquina (opção 2, recusada).
- Prioridade entre canais na fila de espera.

## Abordagens consideradas

- **Número fixo + aviso (escolhida):** previsível; nunca perde live por um pico momentâneo de renderização.
- Automático pela folga da máquina: sem número, mas uma renderização pesada (remoção de música) faria lives
  boas esperarem.

## Para quem constrói

Onde guardar o número da conta, o formato do endpoint do painel e o texto exato (pt/en e demais idiomas
do `frontend/messages`) ficam com quem constrói, seguindo o padrão da página de Lives.
