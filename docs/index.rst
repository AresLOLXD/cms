CMS (OMI fork)
==============

This is the manual of the `OMI fork <https://github.com/AresLOLXD/cms>`_ of
the Contest Management System. It is based on the `upstream manual
<https://cms.readthedocs.io>`_ and adds the fork's own features: Docker
deployment, several contests at once, subtask dependencies and the import of
users from the Admin Web Server.

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   Introduction
   docker-deployment
   docker-scripts
   Installation
   Docker image

.. toctree::
   :maxdepth: 2
   :caption: Preparing a contest

   Creating a contest
   Configuring a contest
   Detailed timing configuration
   Task versioning
   importing-users
   cms-loader
   multi-contest
   migrating-to-multi-contest
   subtask-dependencies

.. toctree::
   :maxdepth: 2
   :caption: Running a contest

   contest-day
   Running CMS
   Troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: Reference

   Task types
   Score types
   RankingWebServer
   ranking-mexico
   rekarel
   External contest formats
   Localization

.. toctree::
   :maxdepth: 2
   :caption: Development

   Internals
   Data model
   API
