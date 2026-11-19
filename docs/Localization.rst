.. _localization:

Localization
************

For developers
==============

When you change a string in a template or in a web server, you have to generate again the file :gh_blob:`cms/locale/cms.pot`. To do so, run this command from the root of the repository.

.. sourcecode:: bash

    ./setup.py extract_messages

When you have a new translation, or an update of an old translation, you need to update the ``cms.mo`` files (the compiled versions of the ``cms.po`` files). You can run ``python setup.py compile_catalog`` to update the ``cms.mo`` files for all translations (or add the ``-l <code>`` argument to update only the one for a given locale; for a language without an AWS catalog also add ``-D cms``, see below). The usual ``python setup.py install`` will do this automatically. Note that, to have the new strings, you need to restart the web server.

The administration web server (AWS) has its own catalog, the ``aws`` domain: the template is :gh_blob:`cms/locale/aws.pot` and the translations are :file:`cms/locale/<code>/LC_MESSAGES/aws.po`. Only the files under ``cms/server/admin`` are extracted (see :gh_blob:`babel_mapping_aws.cfg`). To generate the template again and to merge it into a translation, run these commands from the root of the repository.

.. sourcecode:: bash

    ./setup.py extract_messages -F babel_mapping_aws.cfg -o cms/locale/aws.pot
    ./setup.py update_catalog -D aws -i cms/locale/aws.pot -l <code> --no-fuzzy-matching --ignore-obsolete=1

Babel 2.12.1 (pinned in ``constraints.txt``) needs the ``=1`` in ``--ignore-obsolete=1``, while ``--no-fuzzy-matching`` takes no value. Without ``--ignore-obsolete``, the messages that were removed from the template stay in the catalog as ``#~`` lines.

Every AWS page follows the language of the admin: the one saved in the ``aws_language`` cookie (set by the language selector), else the best match for the ``Accept-Language`` header of the browser, else English. Every text that an AWS template, script or handler shows goes through gettext, and the tests in :gh_blob:`cmstestsuite/unit_tests/server/admin/aws_i18n_guard_test.py` check it. They need no database and cover every template, script and Python file under ``cms/server/admin``:

- no text node, text attribute, script literal, notification, raised error or message constant is left outside gettext, and every page rendered with all its messages marked as translated shows no English word (the intentional exceptions are listed in :gh_blob:`cmstestsuite/unit_tests/server/admin/aws_i18n_lint.py`);
- a control that the page posts under a name never carries a translated value, because a handler compares it with an English key;
- a macro is imported with its context, and the format specifiers of every message are valid;
- the catalogs have every message of the code (the messages of ``cmscommon/importers.py`` are declared with ``N_()`` in ``cms/server/admin/handlers/dataset.py``), none of them untranslated or fuzzy.

If you add English text to AWS, or take new AWS code from upstream (an upstream sync), wrap the text with ``{% trans %}`` or ``_()`` in a template, with ``self._()`` (or ``N_()`` for a constant) in Python, add its Spanish to :file:`cms/locale/es/LC_MESSAGES/aws.po`, and update the catalog with the two commands above, keeping ``--ignore-obsolete=1``. Without that, the tests fail.

``./setup.py compile_catalog`` compiles both the ``cms`` and the ``aws`` catalogs, and skips the languages that have no :file:`aws.po`. With ``-l <code>`` it fails for a language that has no :file:`aws.po`; build only its ``cms`` catalog with ``./setup.py compile_catalog -D cms -l <code>``. AWS offers English and every language that has a compiled ``aws.mo``; without it (for instance in an editable install that never ran ``compile_catalog``) AWS runs in English.


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

    ./setup.py compile_catalog -D cms -l <code>

To translate AWS into a new language, run ``./setup.py init_catalog -D aws -i cms/locale/aws.pot -l <code>`` and translate :file:`cms/locale/<code>/LC_MESSAGES/aws.po` in the same way; it is merged with the ``update_catalog`` command above and compiled with ``./setup.py compile_catalog -l <code>``, which builds both catalogs.
