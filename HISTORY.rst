=========
Changelog
=========

The format is based on `Keep a Changelog: https://keepachangelog.com/en/1.0.0/`,
and this project adheres to `Semantic Versioning: https://semver.org/spec/v2.0.0.html`

Unreleased
----------

Added
^^^^^


Changed
^^^^^^^

Deprecated
^^^^^^^^^^

Removed
^^^^^^^

Fixed
^^^^^

Security
^^^^^^^^

v0.0.2
------

Added
^^^^^
* Better support for R412.
* errors for AT commands and module errors will throw python exceptions.
* setting bands on r4 modules.


Changed
^^^^^^^
* Renamed function to set up the module from init to setup to have a clearer API.
* Improvement of api and made methods common.

v0.0.1
------
First version. Support for SARA-N211 and initial support for SARA-R412