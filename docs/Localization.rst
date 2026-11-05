.. _localization:

Localization
************

For developers
==============

When you change a string in a template or in a web server, you have to generate again the file :gh_blob:`cms/locale/cms.pot`. To do so, run this command from the root of the repository.

.. sourcecode:: bash

    ./setup.py extract_messages

When you have a new translation, or an update of an old translation, you need to update the ``cms.mo`` files (the compiled versions of the ``cms.po`` files). You can run ``python setup.py compile_catalog`` to update the ``cms.mo`` files for all translations (or add the ``-l <code>`` argument to update only the one for a given locale). The usual ``python setup.py install`` will do this automatically. Note that, to have the new strings, you need to restart the web server.

The administration web server (AWS) has its own catalog, the ``aws`` domain: the template is :gh_blob:`cms/locale/aws.pot` and the translations are :file:`cms/locale/<code>/LC_MESSAGES/aws.po`. Only the files under ``cms/server/admin`` are extracted (see :gh_blob:`babel_mapping_aws.cfg`). To generate the template again and to merge it into a translation, run these commands from the root of the repository.

.. sourcecode:: bash

    ./setup.py extract_messages -F babel_mapping_aws.cfg -o cms/locale/aws.pot
    ./setup.py update_catalog -D aws -i cms/locale/aws.pot -l <code>

``./setup.py compile_catalog`` compiles both the ``cms`` and the ``aws`` catalogs. AWS offers English and every language that has a compiled ``aws.mo``; without it (for instance in an editable install that never ran ``compile_catalog``) AWS runs in English.


For translators
===============

To begin translating to a new language, run this command from the root of the repository.

.. sourcecode:: bash

    ./setup.py init_catalog -l <code>

Right after that, open the newly created :file:`cms/locale/<code>/LC_MESSAGES/cms.po` and fill the information in the header. To translate a string, simply fill the corresponding msgstr with the translations. You can also use specialized translation softwares such as poEdit and others.

When the developers update the ``cms.pot`` file, you do not need to start from scratch. Instead, you can create a new ``cms.po`` file that merges the old translated string with the new, to-be-translated ones. The command is the following, run inside the root of the repository.

.. sourcecode:: bash

    ./setup.py update_catalog -l <code>

After you are done translating the messages, please run the following command and check that no error messages are reported (the developers will be glad to assist you if any of them isn't clear):

.. sourcecode:: bash

    ./setup.py compile_catalog -l <code>

To translate AWS into a new language, run ``./setup.py init_catalog -D aws -i cms/locale/aws.pot -l <code>`` and translate :file:`cms/locale/<code>/LC_MESSAGES/aws.po` in the same way; it is merged and compiled with the ``update_catalog`` and ``compile_catalog`` commands above.
