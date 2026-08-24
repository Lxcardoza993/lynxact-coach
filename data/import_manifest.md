# Vault import manifest (2026-08-24)

| clip_id | size | duration | ok |
|---|---|---|---|
| cruyff-turn_johan-cruyff_1974 | 0.4M | 5.7s | ✓ |
| elastico_neymar_2011 | 1.5M | 24.1s | ✓ |
| elastico_rivelino_1970 | 1.3M | 21.5s | ✓ |
| elastico_roberto-rivellino_1970 | 1.3M | 21.5s | ✓ |
| elastico_ronaldinho_2005 | 0.5M | 7.0s | ✓ |
| la-croqueta_andres-iniesta_2010 | 0.6M | 10.0s | ✓ |
| la-croqueta_andrés-iniesta_2010 | 0.6M | 10.0s | ✓ |
| la-croqueta_andrés-iniesta_2012 | 2.3M | 28.1s | ✓ |
| la-croqueta_lionel-messi_2011 | 1.2M | 17.1s | ✓ |
| matthews-feint_cristiano-ronaldo_2007 | 2.3M | 30.0s | ✓ |
| matthews-feint_cristiano-ronaldo_2009 | 0.8M | 16.0s | ✓ |
| matthews-feint_gareth-bale_2014 | 0.8M | 13.0s | ✓ |
| matthews-feint_stanley-matthews_1953 | 1.3M | 28.1s | ✓ |
| step-over_cristiano-ronaldo_2006 | 0.4M | 4.9s | ✓ |
| step-over_garrincha_1962 | 1.5M | 30.0s | ✓ |
| step-over_neymar_2015 | 0.4M | 5.0s | ✓ |
| step-over_robinho_2005 | 0.8M | 12.0s | ✓ |
| step-over_robinho_2007 | 0.6M | 8.5s | ✓ |
| step-over_ronaldo-nazario_1998 | 1.2M | 15.0s | ✓ |
| stop-and-go_arjen-robben_2014 | 0.8M | 14.0s | ✓ |
| stop-and-go_cristiano-ronaldo_2009 | 0.2M | 8.0s | ✓ |
| stop-and-go_kylian-mbappe_2018 | 0.7M | 8.0s | ✓ |
| stop-and-go_kylian-mbappé_2019 | 0.7M | 11.0s | ✓ |
| v-move_falcao_2008 | 0.7M | 8.0s | ✓ |
| v-move_luka-modrić_2022 | 1.2M | 15.0s | ✓ |
| v-move_ricardinho_2016 | 0.6M | 8.0s | ✓ |
| v-move_xavi-hernández_2012 | 0.4M | 8.0s | ✓ |
| v-move_zinedine-zidane_2002 | 0.2M | 4.7s | ✓ |

Imported 28 clips from `/home/li/football-dribbling-vault/sports/football/videos` into `data/vault`.
License: 自有素材(球员动作切片,可自由演示)。
Retired demos skipped: body-feint_diego-maradona_1986.mp4, elastico_ronaldinho_2006.mp4, la-croqueta_lionel-messi_2015.mp4.
Failures: none
注:本地 .env 含 VAULT_ROOT 覆盖(指向旧素材镜像),dev 起 app 会列出镜像库的全部片段;
生产无此变量,VAULT_ROOT=repo/data/vault,即本批导入的 28 个。

| official-film_world-cup_1930 | 74M | 826.9s | ✓ |

第 29 个候选(archive.org):1930 年世界杯官方纪录片《Official Film of the 1930 World Cup》
(乌拉圭 vs 阿根廷决赛),CC-BY-SA 4.0,署名 Archive.org 上传者 + 同方式共享,仅演示用途。

## 远程来源核实结果(2026-08-24 实测)
- SkillCorner 10 场广播视频:✗ 官方无公开视频下载入口(skillcorner.com/opendata 404;
  GitHub opendata 仓库仅 tracking 数据、无视频、无 release assets;视频属商业模式"Data On Demand")。
- Metrica 3 场 sample 视频:✗ 官网 sample-data 页已下线(metrica-sports.com/sample-data 404);
  GitHub sample-data 仓库仅 CSV 数据;更多视频在其对外教程链接中,无稳定直链。
- Commons/archive.org:✓ 仅取 1930 官方纪录片 1 部(CC-BY-SA);现代比赛转播无合规免费源。
