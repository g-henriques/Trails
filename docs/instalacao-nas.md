# Instalar o Trilhos de Portugal na NAS QNAP — passo a passo


Este guia leva-o de uma QNAP com o Container Station até ao Trilhos de Portugal a funcionar, com atualizações automáticas a partir
do GitHub e backups diários. A app fica acessível só na rede local.

## Valores usados neste guia

| Exemplo            | Significado                                                                   |
| ------------------ | ----------------------------------------------------------------------------- |
| `192.168.1.50`     | IP fixo da NAS                                                                |
| `/share/Container` | Pasta partilhada `Container` (criada pelo Container Station)                  |
| **`8094`**      | Porta do Trilhos de Portugal no browser → **`http://192.168.1.50:8094`**                 |
| 8080, 8088, 8090, 8091, 8092 | Portas já usadas por outras apps da NAS (inventário da skill). **Não as use.** |

## O que vai ficar instalado

```
/share/Container/
├── trails/                 ← código do GitHub
│   ├── .env               ← configuração (passo 3)
│   ├── data/              ← dados (criados no 1.º arranque)
│   ├── backups/           ← cópias diárias
│   └── deploy/            ← scripts de atualização, backup, restauro e git
└── trails-ssh/             ← deploy key e token do GHCR (só se o repositório for privado)
```

## 1. Antes de começar

- Container Station em execução e SSH ativo (**Painel de controlo → Rede e Serviços de Ficheiros → Telnet / SSH**).
- `ssh admin@192.168.1.50`. Na QTS 5.1+, no menu "Console Management" escreva `Q` e depois `Y`.
- Confirme que a porta está livre (não pode aparecer nenhuma linha com `:8094`):

  ```sh
  netstat -tln | grep -E ':(8094) '
  ```

## 2. Descarregar o código

A QNAP não traz `git`: usa-se um contentor descartável (`alpine/git`), como fazem os scripts de atualização.

**Repositório público:**

```sh
cd /share/Container
docker run --rm -v /share/Container:/share/Container -w /share/Container \
  alpine/git:latest clone --branch main https://github.com/g-henriques/Trails.git trails
```

**Repositório privado:** crie uma deploy key só para este repositório (o GitHub não deixa reutilizar a de outro). A
chave fica fora de `/root`, porque a QNAP apaga a `/root` a cada reinício.

```sh
mkdir -p /share/Container/trails-ssh
docker run --rm -v /share/Container/trails-ssh:/keys --entrypoint ssh-keygen alpine/git:latest \
  -t ed25519 -f /keys/id_ed25519 -N "" -C "qnap-trails"
chmod 700 /share/Container/trails-ssh && chmod 600 /share/Container/trails-ssh/id_ed25519
cat /share/Container/trails-ssh/id_ed25519.pub
```

No GitHub: **Trails → Settings → Deploy keys → Add deploy key**, cole a linha `ssh-ed25519 …` e deixe **"Allow write
access" desmarcado**. Depois:

```sh
cd /share/Container
docker run --rm -v /share/Container:/share/Container -w /share/Container \
  -e GIT_SSH_COMMAND="ssh -i /share/Container/trails-ssh/id_ed25519 -o IdentitiesOnly=yes -o UserKnownHostsFile=/share/Container/trails-ssh/known_hosts -o StrictHostKeyChecking=accept-new" \
  alpine/git:latest clone --branch main git@github.com:g-henriques/Trails.git trails
```

## 3. Configurar o `.env`

```sh
cd /share/Container/trails
cp .env.example .env
chmod 600 .env
vi .env          # i = escrever, Esc = parar, :wq = gravar e sair
```

Também pode editar pela partilha de rede `\\192.168.1.50\Container\trails\.env`, com um editor que grave com fins de
linha Unix (LF).

| Variável              | Valor                                        | Explicação                                                    |
| --------------------- | -------------------------------------------- | ------------------------------------------------------------- |
| `NAS_PORT`            | `8094`                                    | Porta no browser.                                             |
| `NAS_BIND_IP`         | `192.168.1.50`                               | IP fixo da NAS: a app não fica disponível noutras interfaces. |
| `NAS_SSH_KEY`         | `/share/Container/trails-ssh/id_ed25519`      | Só para repositórios privados.                                |
| `NAS_IMAGE`           | `trails:local`                                | Construir na NAS, ou `ghcr.io/…` (modo GHCR, passo 7).        |
| `NAS_GHCR_TOKEN_FILE` | _(vazio)_                                    | Só no modo GHCR com pacote privado.                           |
| `TRAILS_SCRAPE_INTERVAL_HOURS` | `168`                               | Horas entre recolhas completas dos trilhos (168 = semanal; 0 = só manual). |
| `TRAILS_REQUEST_DELAY` | `1`                                         | Segundos entre pedidos ao mesmo site. O OpenStreetMap pede uso moderado: não baixe de 1. |
| `TRAILS_OVERPASS_URLS` | _(vazio)_                                   | Servidores Overpass para descobrir trilhos fora das listas; vazio = públicos, `off` = desligar. |

Valide: `docker compose config --quiet && echo "configuração OK"`.

## 4. Arrancar

```sh
cd /share/Container/trails
./deploy/nas-update.sh --force
```

Constrói a imagem, prepara a pasta de dados, arranca e espera até a app responder. Termina com
`trails … is up: http://192.168.1.50:8094`. Os contentores voltam a arrancar sozinhos depois de um reinício da NAS.
Não altere a configuração no Container Station: altere o `.env` e corra `docker compose up -d`.

## 5. Verificar

```sh
docker compose ps                                  # running (healthy)
wget -qO- http://192.168.1.50:8094/healthz      # resposta do health check
```

No browser: **`http://192.168.1.50:8094`**.

### A primeira recolha de trilhos

Logo depois do arranque, a app recolhe os trilhos sozinha (e volta a fazê-lo uma vez por semana):

1. **Listas oficiais** (uns segundos): percursos homologados PR/PL/GR por distrito e concelho, da Wikipédia e da
   wiki do OpenStreetMap. A lista e o mapa por distritos ficam logo disponíveis.
2. **Traçados GPX** (10–30 minutos): cada percurso que existe no OpenStreetMap é descarregado com uma pausa de 1 s
   entre pedidos. O mapa vai ganhando trilhos à medida que chegam; o progresso aparece no topo do site.

O botão **Atualizar** no topo do site força uma nova recolha. Uma recolha que falhe não apaga os dados anteriores.

## 6. Atualizações automáticas e backups (cron)

```sh
./deploy/backup.sh          # cria backups/trails-AAAAMMDD-HHMMSS.*.gz
./deploy/nas-update.sh      # "Already at …; nothing to do"
```

Agende no `/etc/config/crontab` (o ficheiro que sobrevive a reinícios), nos horários livres do inventário da NAS:

```sh
cat >> /etc/config/crontab <<'EOF'
# Trilhos de Portugal: backup às 03:30 e atualização às 04:45
30 3 * * * /share/Container/trails/deploy/backup.sh >> /share/Container/trails/backup.log 2>&1
45 4 * * * /share/Container/trails/deploy/nas-update.sh >> /share/Container/trails/update.log 2>&1
EOF
crontab /etc/config/crontab && /etc/init.d/crond.sh restart
crontab -l | grep trails
```

Se o backup falhar, o `backup.sh` escreve `Backup FAILED` no `backup.log`, sai com erro e mantém os backups anteriores.
O `nas-update.sh` não atualiza sem backup.

> Tudo o que entra no `main` acaba a correr na NAS. No GitHub, proteja o `main`: PR obrigatório e checks obrigatórios.

## 7. Modo GHCR (opcional)

Evita construir a imagem na NAS. O CI publica a imagem para x86-64 e ARM64 a cada push para o `main`, só com os
testes verdes.

1. `.env`: `NAS_IMAGE=ghcr.io/g-henriques/trails:latest`.
2. Pacote privado: crie um _Personal access token (classic)_ só com `read:packages` e guarde-o numa pasta
   partilhada. Não use `docker login` à mão: o login fica em `/root` e perde-se no próximo reinício.

   ```sh
   vi /share/Container/trails-ssh/ghcr-token && chmod 600 /share/Container/trails-ssh/ghcr-token
   ```

   `.env`: `NAS_GHCR_TOKEN_FILE=/share/Container/trails-ssh/ghcr-token`.
3. `./deploy/nas-update.sh --force`.

## 8. Backups para fora da NAS

No **Hybrid Backup Sync 3**, copie `Container/trails/backups` (e o `.env`, ou guarde os segredos noutro sítio) para um
disco USB ou para a nuvem, todos os dias, depois do backup.

## 9. Restaurar e voltar atrás

```sh
ls -1t backups/ | head
./deploy/restore.sh backups/<ficheiro>
```

O script recusa backups incompletos, pede confirmação, para a app, repõe os dados e volta a arrancá-la.

Versão anterior: `./deploy/git.sh log --oneline -5`, `./deploy/git.sh checkout <commit>`, restaure o backup feito
antes da atualização e `docker compose up -d --build` (modo GHCR: `NAS_IMAGE=…:sha-<commit>`). Para voltar a seguir o
`main`: `./deploy/git.sh checkout main`.

## 10. Rede

Não crie port forwarding no router para a `8094` e desligue o UPnP (e a configuração automática do router no
myQNAPcloud). O QuFirewall pode não filtrar portas publicadas por contentores: é o `NAS_BIND_IP` que limita a app à
rede local. Para acesso remoto, use uma VPN (QVPN, WireGuard, Tailscale).

## 11. Problemas comuns

| Sintoma                                          | Solução                                                                                        |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `docker: command not found`                      | O Container Station tem de estar em execução; volte a abrir a sessão SSH.                      |
| `port is already allocated`                      | Outra app usa a porta. Escolha uma livre em `NAS_PORT` e corra `docker compose up -d`.          |
| `cannot assign requested address`                | O `NAS_BIND_IP` não é o IP atual da NAS.                                                       |
| `Permission denied (publickey)`                  | Confirme a deploy key no GitHub e `NAS_SSH_KEY`. Teste: `./deploy/git.sh fetch origin main`.    |
| `unauthorized` / `denied` no `ghcr.io`           | Falta o token ou expirou: `NAS_GHCR_TOKEN_FILE` (passo 7).                                     |
| `Backup FAILED`                                  | Veja a linha anterior no `backup.log`; os backups antigos estão intactos.                      |
| As tarefas cron não correm                       | Confirme `/etc/config/crontab` e corra `crontab /etc/config/crontab && /etc/init.d/crond.sh restart`. |
| "Ainda não há trilhos" no site                   | A 1.ª recolha arranca uns segundos depois do arranque. Veja o progresso no topo do site ou em `docker compose logs -f app`. |
| Muitos trilhos "sem GPX"                         | Só há traçado para os trilhos que já estão desenhados no OpenStreetMap. Confirme também que a NAS chega a `overpass-api.de` (`docker compose logs app \| grep -i overpass`): é o que encontra a maioria dos traçados. |
| `Overpass discovery skipped` nos logs            | Os servidores Overpass estavam em baixo ou lentos; a lista e os traçados já conhecidos continuam a funcionar e a próxima recolha volta a tentar. Pode forçar uma no botão **Atualizar**. |
| A recolha falha sempre                           | A app precisa de Internet (Wikipédia, wiki.openstreetmap.org, api.openstreetmap.org, overpass-api.de). Veja o erro em `http://192.168.1.50:8094/api/status`. |

## 12. Desinstalar

```sh
cd /share/Container/trails
./deploy/backup.sh
docker compose down --rmi local
# rm -rf /share/Container/trails /share/Container/trails-ssh   # apaga tudo, incluindo os dados
```

Remova as linhas do `/etc/config/crontab` e apague a deploy key no GitHub.
