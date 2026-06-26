#import "../../driver/template.typ": *
#import "network-diagram.typ": diagram

#let title = "Iris Network"
#let subtitle = "That is AS4242421023 on dn42."

#show: article.with(title: title, subtitle: subtitle)

This article is about the Iris Network, yet another _experimental_ network, operated by Iris, that runs on #link("https://dn42.dev/", "dn42").

== Network

Iris Network owns AS4242421023, _mainly_ announcing `172.21.111.64/26` and `fd42:4242:1023::/48`.

=== Nodes

As of 2026-05-19, Iris Network has 7 nodes in 4 countries, with 3 nodes in the United States, and 1 node in each of Hong Kong, Japan, the Netherlands and France.

=== Topology

Iris Network has a simple topology, with all nodes forming a full mesh with WireGuard tunnels when possible
#footnote[It is, _as of the time of writing_.]
, _i.e._, when a pair of nodes share a common IP version. Babel is used as the interior routing protocol.

A diagram of the topology is shown below.

#figure(alt: "Full mesh of 7 nodes: lax-01, tyo-01, hkg-01, lax-02, dls-01, ams-01, par-01", caption: "Yes, It's full mesh")[
  #diagram
]

#pagebreak()

=== BGP

Iris Network _exists_ for BGP experimentation. The BIRD Internet Routing Daemon is deployed on all nodes to provide BGP (as well as Babel) services.

For iBGP, three nodes `lax-01`, `tyo-01` and `par-01` serve as route reflectors. Route reflectors form a full mesh with each other, and all other nodes peer with the reflectors.

Iris Network actively peers with other dn42 participants. As of the time of writing, there are more than 200 active peering sessions with 53 unique ASNs.
Previously, peering sessions were established manually, but now the autopeer portal has been doing a large portion of the work.

As of the time of writing, all nodes but `par-01` accept and have established peering sessions.

=== Infrastructure as Code

Iris Network manages its nodes and peering sessions with Ansible. Playbooks cover system configuration, WireGuard tunnel setup, BIRD configuration, DNS, the looking glass, the automatic peering system, websites and monitoring.

GitHub Actions workflows are used along with Ansible to validate and apply autopeer requests. Due to GitHub's well-known limitation of not supporting IPv6 in Actions runners, a self-hosted runner is deployed on an off network node `fra-01` to handle the Ansible playbook runs.

=== Looking Glass

A custom looking glass is deployed across the network.

The looking glass is accessible at #link("https://network.owo.li", "network.owo.li"), or #link("https://network.iris.dn42", "network.iris.dn42") if the reader prefers dn42 access.

=== Autopeering

Iris Network has been offering self service autopeering through a portal at #link("https://autopeer.owo.li", "autopeer.owo.li") and #link("https://autopeer.iris.dn42", "autopeer.iris.dn42") since April the 18th, 2026.

#info[
  _TODO_: Describe the flow of the autopeering system, and how it works with GitHub Actions and Ansible.
]

=== Monitoring

An off network node `fra-01` runs Prometheus and scrapes metrics from all nodes every 30 seconds. Each node exports system metrics, BIRD routing metrics, and WireGuard tunnel statistics. A smokeping prober on every node measures latency to all other nodes via ICMPv6.

A FlapAlerted instance on `ams-01` receives the full BGP table over iBGP sessions from all nodes and monitors for route flaps, exposing a dn42-only #link("https://flapalerted.iris.dn42", "web dashboard"), as well as exposing the metrics for Prometheus to collect.

A public Grafana dashboard at #link("https://metrics.intranet.services", "metrics.intranet.services") provides aggregate views of the network's health and performance over time.

== Miscellaneous

- Iris Network is a part of the XJBCast Project, announcing `172.23.41.80/28` and `fd32:3940:2738::/48` for the project.
- `fra-01`, while not being a node of Iris Network, does have its presence in dn42.

== Open Source

The infrastructure, looking glass and autopeering portal of Iris Network are open source, and available at the following repositories:

- #link("https://github.com/OwO-Intranet-Services/dn42-network", "OwO-Intranet-Services/dn42-network") contains the Ansible playbooks, GitHub Action workflows for autopeering, and up-to-date network configuration.
- #link("https://github.com/el-ev/bird-lg-rs", "el-ev/bird-lg-rs") contains the source code of the looking glass and autopeering site.
