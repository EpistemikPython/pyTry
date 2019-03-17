#
# main.py -- parse a Monarch text file
#            and create Gnucash transactions from the data
#
# Copyright (c) 2018, 2019 Mark Sattolo <epistemik@gmail.com>
#
# @author Mark Sattolo <epistemik@gmail.com>
# @revised 2019-03-16
# @version Python3.6


import os.path as osp
from sys import argv, exit
from parseMonarch import parse_monarch_report
from createGnucashTxs import create_gnc_txs
from Configuration import *


def main():
    exe = argv[0].split('/')[-1]
    if len(argv) < 4:
        print_error("NOT ENOUGH parameters!")
        print_info("usage: python {0} <monarch file> <gnucash file> <mode: prod|test>".format(exe), CYAN)
        print_info("Example: {0} '{1}' '{2}' 'test'".format(exe, "txtFromPdf/Monarch-Mark-all.txt", TEST1_GNC), CYAN)
        exit(4)

    mon_file = argv[1]
    if not osp.isfile(mon_file):
        print_error("File path '{}' does not exist. Exiting...".format(mon_file))
        exit(1)

    gnc_file = argv[2]
    if not osp.isfile(gnc_file):
        print_error("File path '{}' does not exist. Exiting...".format(gnc_file))
        exit(2)

    mode = argv[3]

    # parse an external Monarch report file
    tx_colxn = parse_monarch_report(mon_file, mode)

    # create gnucash transactions and write to the desired Gnucash file
    create_gnc_txs(tx_colxn, gnc_file, mode)

    print_info("\n >>> PROGRAM ENDED.", GREEN)


if __name__ == '__main__':
    main()
