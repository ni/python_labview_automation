Python LabVIEW Automation (labview_automation)
==============================================

Python LabVIEW Automation labview_automation is a Python package to make it
easy to call LabVIEW VirtualInstruments (VIs) from Python.  It includes a
Pythonic interface to call VIs and a class to interact with LabVIEW executables
on Windows.

To facilitate this interaction LabVIEW is started with a VI that listens for
tcp messages.  The Python interface sends BSON (see bsonspec.org) encoded
messages to the VI which then performs the commands.

LabVIEW can be started on a remote machine using hoplite.  Since the interface
between Python and LabVIEW is tcp the messages can be sent to another machine.

Installation
------------
labview_automation can be installed by cloning the master branch and then
in a command line in the directory of setup.py run:

    pip install --pre .


Simple Local Example
--------------------
You can set controls on the front panel of the VIs that you execute by adding
members to a dictionary where each member represents a different control of
the given name.  Controls can be numerics, strings, booleans, arrays, or
clusters of the same types.

`run_vi_synchronous` runs the VI synchronously and returns a dictionary of
all the indicators on the VI.  

    from labview_automation import LabVIEW
    lv = LabVIEW()
    lv.start() # Launches the active LabVIEW with the listener VI
    with lv.client() as c:
        control_values = {
            "DBL Control": 5.0,
            "String Control": "Hello World!",
            "Error In": {
                "status": False,
                "code": 0,
                "source": ""
            }
        }
        indicators = c.run_vi_synchronous(
            vi_path, control_values)
        print(indicators['Result'])
        error_message = c.describe_error(indicators['Error Out'])
    lv.kill() # Stop LabVIEW

Development
-----------
All LabVIEW code is developed using LabVIEW 2014 SP1 x86.

Pull requests for Python code should adhere to PEP8.

License
-------
The MIT License (MIT)
Copyright (c) 2016 National Instruments

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
