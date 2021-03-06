###############################################################################################################################
# coding=utf-8
#
# createGnucashTxs.py -- parse a Monarch record, possibly from a json file,
#                        create Gnucash transactions from the data and write to a Gnucash file
#
# Copyright (c) 2019 Mark Sattolo <epistemik@gmail.com>
#
__author__ = 'Mark Sattolo'
__author_email__ = 'epistemik@gmail.com'
__python_version__ = 3.6
__created__ = '2018'
__updated__ = '2019-06-02'

import copy
import json
import re
from gnucash import Session, Transaction, Split, GncNumeric, GncPrice
from gnucash.gnucash_core_c import CREC
from Configuration import *


# noinspection PyUnresolvedReferences,PyUnboundLocalVariable
class GncTxCreator:
    """
    create Gnucash transactions and prices from Monarch json
    """
    def __init__(self, tx_colxn, gnc_f, md, pdb=None, bk=None, rt=None, cur=None, rpinfo=None):
        print_info("createGnucashTxs:GncTxCreator()\nRuntime = {}\n".format(strnow), MAGENTA)
        self.tx_coll  = tx_colxn
        self.gnc_file = gnc_f
        self.mode     = md
        self.price_db = pdb
        self.book     = bk
        self.root     = rt
        self.curr     = cur
        self.report_info = rpinfo

    gncu = GncUtilities()

    def get_mon_pdf_info(self, mtx, plan_type, ast_parent, rev_acct):
        """
        Asset accounts: use the proper path to find the parent then search for the Fund Code in the descendants
        Revenue accounts: pick the proper account based on owner and plan type
        gross_curr: re match to Gross then concatenate the two match groups
        date: re match to get day, month and year then re-assemble to form Gnc date
        Units: re match and concatenate the two groups on either side of decimal point
        Description: use DESC and Fund Code
        Notes: use 'Unit Balance' and UNIT_BAL
        :param        mtx:   dict: Monarch transaction information
        :param  plan_type: String:
        :param ast_parent: String:
        :param   rev_acct: Gnucash account
        :return: dict, dict
        """
        print_info('get_mon_pdf_info()', MAGENTA)

        # set the regex needed to match the required groups in each value
        re_switch = re.compile(r"^(" + SWITCH + ")-([InOut]{2,3}).*")
        re_intrf  = re.compile(r"^(" + INTRF + ")-([InOut]{2,3}).*")
        re_gross  = re.compile(r"^(\(?)\$([0-9,]{1,6})\.([0-9]{2})\)?.*")
        re_units  = re.compile(r"^(-?)([0-9]{1,5})\.([0-9]{4}).*")

        init_tx = {FUND_CMPY: mtx[FUND_CMPY]}

        print_info("trade date = {}".format(mtx[TRADE_DATE]))
        trade_date = mtx[TRADE_DATE].split('/')
        init_tx[TRADE_DAY] = int(trade_date[1])
        init_tx[TRADE_MTH] = int(trade_date[0])
        init_tx[TRADE_YR]  = int(trade_date[2])
        print_info("trade day/month/year = '{}/{}/{}'".format(init_tx[TRADE_DAY],init_tx[TRADE_MTH],init_tx[TRADE_YR]))

        # check if we have a switch/transfer
        switch = True if (re.match(re_switch, mtx[DESC]) or re.match(re_intrf, mtx[DESC])) else False
        init_tx[SWITCH] = switch
        print_info("{}Have a Switch!".format('DO NOT ' if not switch else '>>> '), BLUE)

        asset_acct_name = mtx[FUND_CMPY] + " " + mtx[FUND_CODE]
        asset_parent = ast_parent
        # special locations for Trust Revenue and Asset accounts
        if asset_acct_name == TRUST_AST_ACCT:
            asset_parent = self.root.lookup_by_name(TRUST)
            print_info("asset_parent = {}".format(asset_parent.GetName()))
            rev_acct = self.root.lookup_by_name(TRUST_REV_ACCT)
            print_info("rev_acct = {}".format(rev_acct.GetName()))
        # save the (possibly modified) Revenue account to the Gnc tx
        init_tx[REVENUE] = rev_acct

        # get the asset account
        asset_acct = asset_parent.lookup_by_name(asset_acct_name)
        if asset_acct is None:
            raise Exception("Could NOT find acct '{}' under parent '{}'".format(asset_acct_name, asset_parent.GetName()))
        else:
            init_tx[ACCT] = asset_acct
            print_info("asset_acct = {}".format(asset_acct.GetName()), color=CYAN)

        # get the dollar value of the tx
        re_match = re.match(re_gross, mtx[GROSS])
        if re_match:
            str_gross_curr = re_match.group(2) + re_match.group(3)
            # remove possible comma
            gross_curr = int(str_gross_curr.replace(',', ''))
            # if match group 1 is not empty, amount is negative
            if re_match.group(1) != '':
                gross_curr *= -1
            print_info("gross_curr = {}".format(gross_curr))
            init_tx[GROSS] = gross_curr
        else:
            raise Exception("PROBLEM!! re_gross DID NOT match with value '{}'!".format(mtx[GROSS]))

        # get the units of the tx
        re_match = re.match(re_units, mtx[UNITS])
        if re_match:
            units = int(re_match.group(2) + re_match.group(3))
            # if match group 1 is not empty, units is negative
            if re_match.group(1) != '':
                units *= -1
            init_tx[UNITS] = units
            print_info("units = {}".format(units))
        else:
            raise Exception("PROBLEM!! re_units DID NOT match with value '{}'!".format(mtx[UNITS]))

        # assemble the Description string
        descr = "{}: {} {}".format(COMPANY_NAME[init_tx[FUND_CMPY]], mtx[DESC], asset_acct_name)
        init_tx[DESC] = descr
        print_info("descr = {}".format(descr))

        # notes field
        notes = str(asset_acct_name + " balance = " + mtx[UNIT_BAL])
        init_tx[NOTES] = notes
        print_info("notes = {}".format(notes))

        pair_tx = None
        have_pair = False
        if switch:
            print_info("Tx is a Switch to OTHER Monarch account.", BLUE)
            # look for switches in this plan type with same company, day, month and opposite gross value
            for itx in self.report_info.plans[plan_type]:
                if itx[SWITCH] and itx[FUND_CMPY] == init_tx[FUND_CMPY] and itx[GROSS] == (gross_curr * -1) \
                        and itx[TRADE_DAY] == init_tx[TRADE_DAY] and itx[TRADE_MTH] == init_tx[TRADE_MTH] :
                    # ALREADY HAVE THE FIRST ITEM OF THE PAIR
                    have_pair = True
                    pair_tx = itx
                    print_info('Found the MATCH of a pair...', YELLOW)
                    break

            if not have_pair:
                # store the tx until we find the matching tx
                self.report_info.plans[plan_type].append(init_tx)
                print_info('Found the FIRST of a pair...\n', YELLOW)

        return init_tx, pair_tx

    def get_mon_copy_info(self, mtx, plan_type, ast_parent, rev_acct):
        """
        parse the Monarch transactions from a copy&paste json file
        Asset accounts: use the proper path to find the parent then search for the Fund Code in the descendants
        Revenue accounts: pick the proper account based on owner and plan type
        gross_curr: re match to Gross then concatenate the two match groups
        date: convert the date then get day, month and year to form a Gnc date
        Units: re match and concatenate the two groups on either side of decimal point
        Description: use DESC and Fund Company
        :param        mtx:   dict: Monarch copied transaction information
        :param  plan_type: String:
        :param ast_parent: String:
        :param   rev_acct: Gnucash account
        :return: dict, dict
        """
        print_info('get_mon_copy_info()', MAGENTA)

        # set the regex needed to match the required groups in each value
        re_gross  = re.compile(r"^(-?)\$([0-9,]{1,6})\.([0-9]{2}).*")
        re_units  = re.compile(r"^(-?)([0-9]{1,5})\.([0-9]{4}).*")

        init_tx = {FUND_CMPY: mtx[FUND_CMPY]}

        # print_info("trade date = {}".format(mtx[TRADE_DATE]))
        conv_date = dt.strptime(mtx[TRADE_DATE], "%d-%b-%Y")
        # print_info("converted date = {}".format(conv_date))
        init_tx[TRADE_DAY] = conv_date.day
        init_tx[TRADE_MTH] = conv_date.month
        init_tx[TRADE_YR]  = conv_date.year
        print_info("trade day-month-year = '{}-{}-{}'".format(init_tx[TRADE_DAY],init_tx[TRADE_MTH],init_tx[TRADE_YR]))

        # check if we have a switch-in/out
        switch = True if mtx[DESC] == SW_IN or mtx[DESC] == SW_OUT else False
        init_tx[SWITCH] = switch
        print_info("{}Have a Switch!".format('DO NOT ' if not switch else '>>> '), BLUE)

        asset_acct_name = mtx[FUND_CMPY] + " " + mtx[FUND_CODE]
        asset_parent = ast_parent
        # special locations for Trust Revenue and Asset accounts
        if asset_acct_name == TRUST_AST_ACCT:
            asset_parent = self.root.lookup_by_name(TRUST)
            print_info("asset_parent = {}".format(asset_parent.GetName()))
            rev_acct = self.root.lookup_by_name(TRUST_REV_ACCT)
            print_info("rev_acct = {}".format(rev_acct.GetName()))
        # save the (possibly modified) Revenue account to the Gnc tx
        init_tx[REVENUE] = rev_acct

        # get the asset account
        asset_acct = asset_parent.lookup_by_name(asset_acct_name)
        if asset_acct is None:
            raise Exception("Could NOT find acct '{}' under parent '{}'".format(asset_acct_name, asset_parent.GetName()))
        else:
            init_tx[ACCT] = asset_acct
            print_info("asset_acct = {}".format(asset_acct.GetName()), color=CYAN)

        # get the dollar value of the tx
        re_match = re.match(re_gross, mtx[GROSS])
        if re_match:
            str_gross_curr = re_match.group(2) + re_match.group(3)
            # remove possible comma
            gross_curr = int(str_gross_curr.replace(',', ''))
            # if match group 1 is not empty, amount is negative
            if re_match.group(1) != '':
                gross_curr *= -1
            print_info("gross_curr = {}".format(gross_curr))
            init_tx[GROSS] = gross_curr
        else:
            raise Exception("PROBLEM!! re_gross DID NOT match with value '{}'!".format(mtx[GROSS]))

        # get the units of the tx
        re_match = re.match(re_units, mtx[UNITS])
        if re_match:
            units = int(re_match.group(2) + re_match.group(3))
            # if match group 1 is not empty, units is negative
            if re_match.group(1) != '':
                units *= -1
            init_tx[UNITS] = units
            print_info("units = {}".format(units))
        else:
            raise Exception("PROBLEM!! re_units DID NOT match with value '{}'!".format(mtx[UNITS]))

        # assemble the Description string
        descr = "{}: {} {}".format(COMPANY_NAME[init_tx[FUND_CMPY]], mtx[DESC], asset_acct_name)
        init_tx[DESC] = descr
        # print_info("descr = {}".format(init_tx[DESC]))

        # notes/load field
        load = str(asset_acct_name + " load = " + mtx[LOAD])
        init_tx[NOTES] = load
        # print_info("notes = {}".format(init_tx[NOTES]))

        pair_tx = None
        have_pair = False
        if switch:
            print_info("Tx is a Switch to OTHER Monarch account.", BLUE)
            # look for switches in this plan type with same company, day, month and opposite gross value
            for itx in self.report_info.plans[plan_type]:
                if itx[SWITCH] and itx[FUND_CMPY] == init_tx[FUND_CMPY] and itx[GROSS] == (gross_curr * -1) \
                        and itx[TRADE_DAY] == init_tx[TRADE_DAY] and itx[TRADE_MTH] == init_tx[TRADE_MTH] :
                    # ALREADY HAVE THE FIRST ITEM OF THE PAIR
                    have_pair = True
                    pair_tx = itx
                    print_info('Found the MATCH of a pair...', YELLOW)
                    break

            if not have_pair:
                # store the tx until we find the matching tx
                self.report_info.plans[plan_type].append(init_tx)
                print_info('Found the FIRST of a pair...\n', YELLOW)

        return init_tx, pair_tx

    # TODO: separate file with standard functions to create Gnucash session, prices, transactions
    def create_gnc_prices(self, tx1, tx2):
        """
        create and load Gnucash prices to the Gnucash PriceDB
        :param tx1: first transaction
        :param tx2: matching transaction, if exists
        :return: nil
        """
        print_info('create_gnc_prices()', MAGENTA)
        pr_date = dt(tx1[TRADE_YR], tx1[TRADE_MTH], tx1[TRADE_DAY])
        datestring = pr_date.strftime("%Y-%m-%d")

        int_price = int((tx1[GROSS] * 100) / (tx1[UNITS] / 10000))
        val = GncNumeric(int_price, 10000)
        print_info("Adding: {}[{}] @ ${}".format(tx1[ACCT].GetName(), datestring, val))

        pr1 = GncPrice(self.book)
        pr1.begin_edit()
        pr1.set_time64(pr_date)
        comm = tx1[ACCT].GetCommodity()
        print_info("Commodity = {}:{}".format(comm.get_namespace(), comm.get_printname()))
        pr1.set_commodity(comm)

        pr1.set_currency(self.curr)
        pr1.set_value(val)
        pr1.set_source_string("user:price")
        pr1.set_typestr('nav')
        pr1.commit_edit()

        if tx1[SWITCH]:
            # get the price for the paired Tx
            int_price = int((tx2[GROSS] * 100) / (tx2[UNITS] / 10000))
            val = GncNumeric(int_price, 10000)
            print_info("Adding: {}[{}] @ ${}".format(tx2[ACCT].GetName(), datestring, val))

            pr2 = GncPrice(self.book)
            pr2.begin_edit()
            pr2.set_time64(pr_date)
            comm = tx2[ACCT].GetCommodity()
            print_info("Commodity = {}:{}".format(comm.get_namespace(), comm.get_printname()))
            pr2.set_commodity(comm)

            pr2.set_currency(self.curr)
            pr2.set_value(val)
            pr2.set_source_string("user:price")
            pr2.set_typestr('nav')
            pr2.commit_edit()

        if self.mode == PROD:
            print_info("Mode = {}: Add Price1 to DB.".format(self.mode), GREEN)
            self.price_db.add_price(pr1)
            if tx1[SWITCH]:
                print_info("Mode = {}: Add Price2 to DB.".format(self.mode), GREEN)
                self.price_db.add_price(pr2)
        else:
            print_info("Mode = {}: ABANDON Prices!\n".format(self.mode), RED)

    # TODO: separate file with standard functions to create Gnucash session, prices, transactions
    def create_gnc_txs(self, tx1, tx2):
        """
        create and load Gnucash transactions to the Gnucash file
        :param tx1: first transaction
        :param tx2: matching transaction if a switch
        :return: nil
        """
        print_info('create_gnc_txs()', MAGENTA)
        # create a gnucash Tx
        gtx = Transaction(self.book)
        # gets a guid on construction

        gtx.BeginEdit()

        gtx.SetCurrency(self.curr)
        gtx.SetDate(tx1[TRADE_DAY], tx1[TRADE_MTH], tx1[TRADE_YR])
        # print_info("gtx date = {}".format(gtx.GetDate()), BLUE)
        print_info("tx1[DESC] = {}".format(tx1[DESC]), YELLOW)
        gtx.SetDescription(tx1[DESC])

        # create the ASSET split for the Tx
        spl_ast = Split(self.book)
        spl_ast.SetParent(gtx)
        # set the account, value, and units of the Asset split
        spl_ast.SetAccount(tx1[ACCT])
        spl_ast.SetValue(GncNumeric(tx1[GROSS], 100))
        spl_ast.SetAmount(GncNumeric(tx1[UNITS], 10000))

        if tx1[SWITCH]:
            # create the second ASSET split for the Tx
            spl_ast2 = Split(self.book)
            spl_ast2.SetParent(gtx)
            # set the Account, Value, and Units of the second ASSET split
            spl_ast2.SetAccount(tx2[ACCT])
            spl_ast2.SetValue(GncNumeric(tx2[GROSS], 100))
            spl_ast2.SetAmount(GncNumeric(tx2[UNITS], 10000))
            # set Actions for the splits
            spl_ast2.SetAction("Buy" if tx1[UNITS] < 0 else "Sell")
            spl_ast.SetAction("Buy" if tx1[UNITS] > 0 else "Sell")
            # combine Notes for the Tx and set Memos for the splits
            gtx.SetNotes(tx1[NOTES] + " | " + tx2[NOTES])
            spl_ast.SetMemo(tx1[NOTES])
            spl_ast2.SetMemo(tx2[NOTES])
        else:
            # the second split is for a REVENUE account
            spl_rev = Split(self.book)
            spl_rev.SetParent(gtx)
            # set the Account, Value and Reconciled of the REVENUE split
            spl_rev.SetAccount(tx1[REVENUE])
            rev_gross = tx1[GROSS] * -1
            # print_info("revenue gross = {}".format(rev_gross))
            spl_rev.SetValue(GncNumeric(rev_gross, 100))
            spl_rev.SetReconcile(CREC)
            # set Notes for the Tx
            gtx.SetNotes(tx1[NOTES])
            # set Action for the ASSET split
            action = FEE if FEE in tx1[DESC] else ("Sell" if tx1[UNITS] < 0 else DIST)
            print_info("action = {}".format(action))
            spl_ast.SetAction(action)

        # ROLL BACK if something went wrong and the two splits DO NOT balance
        if not gtx.GetImbalanceValue().zero_p():
            print_error("gtx Imbalance = {}!! Roll back transaction changes!".format(gtx.GetImbalanceValue().to_string()))
            gtx.RollbackEdit()
            return

        if self.mode == PROD:
            print_info("Mode = {}: Commit transaction changes.\n".format(self.mode), GREEN)
            gtx.CommitEdit()
        else:
            print_info("Mode = {}: Roll back transaction changes!\n".format(self.mode), RED)
            gtx.RollbackEdit()

    def process_monarch_txs(self, mtx, plan_type, ast_parent, rev_acct):
        """
        Asset accounts: use the proper path to find the parent then search for the Fund Code in the descendants
        Revenue accounts: pick the proper account based on owner and plan type
        gross_curr: re match to Gross then concatenate the two match groups
        date: re match to get day, month and year then re-assemble to form Gnc date
        Units: re match and concatenate the two groups on either side of decimal point
        Description: use DESC and Fund Code
        Notes: use 'Unit Balance' and UNIT_BAL
        :param mtx:
        :param plan_type:
        :param rev_acct:
        :param ast_parent:
        :return: nil
        """
        print_info('process_monarch_txs()', MAGENTA)
        try:
            # get the additional required information from the Monarch json
            tx1, tx2 = self.get_mon_copy_info(mtx, plan_type, ast_parent, rev_acct)

            # just return if there is a matching tx but we don't have it yet
            if tx1[SWITCH] and tx2 is None:
                return

            self.create_gnc_prices(tx1, tx2)

            self.create_gnc_txs(tx1, tx2)

        except Exception as ie:
            print_error("process_monarch_txs() EXCEPTION!! '{}'\n".format(str(ie)))

    def create_gnucash_info(self):
        """
        process each transaction in the Monarch input file to get the required Gnucash information
        :return: nil
        """
        print_info("create_gnucash_info()", MAGENTA)
        self.root = self.book.get_root_account()
        self.root.get_instance()

        self.price_db = self.book.get_price_db()
        self.price_db.begin_edit()
        print_info("self.price_db.begin_edit()", CYAN)

        commod_tab = self.book.get_table()
        self.curr = commod_tab.lookup("ISO4217", "CAD")

        for plan_type in self.tx_coll[PLAN_DATA]:
            print_info("\n\t\u0022Plan type = {}\u0022".format(plan_type), YELLOW)

            asset_parent, rev_acct = self.get_plan_info(plan_type)

            for mon_tx in self.tx_coll[PLAN_DATA][plan_type]:
                self.process_monarch_txs(mon_tx, plan_type, asset_parent, rev_acct)

    def get_plan_info(self, plan_type):
        """
        get the required asset and/or revenue information from each plan
        :param plan_type: string: see Configuration
        :return: Gnucash account, Gnucash account: revenue account and asset parent account
        """
        print_info("get_plan_info()", MAGENTA)
        rev_path = copy.copy(ACCT_PATHS[REVENUE])
        rev_path.append(plan_type)
        ast_parent_path = copy.copy(ACCT_PATHS[ASSET])
        ast_parent_path.append(plan_type)

        pl_owner = self.report_info.get_owner()
        if plan_type != PL_OPEN:
            if pl_owner == '':
                raise Exception("PROBLEM!! Trying to process plan type '{}' but NO Owner value found"
                                " in Tx Collection!!".format(plan_type))
            rev_path.append(ACCT_PATHS[pl_owner])
            ast_parent_path.append(ACCT_PATHS[pl_owner])
        print_info("rev_path = {}".format(str(rev_path)))

        rev_acct = self.gncu.account_from_path(self.root, rev_path)
        print_info("rev_acct = {}".format(rev_acct.GetName()))
        print_info("asset_parent_path = {}".format(str(ast_parent_path)))
        asset_parent = self.gncu.account_from_path(self.root, ast_parent_path)
        print_info("asset_parent = {}".format(asset_parent.GetName()))

        return asset_parent, rev_acct

    # TODO: separate file with standard functions to create Gnucash session, prices, transactions
    def prepare_session(self):
        """
        Take the information from a transaction collection and produce Gnucash transactions to write to a Gnucash file
        :return: message
        """
        print_info("prepare_session()", MAGENTA)
        msg = TEST
        try:
            session = Session(self.gnc_file)
            self.book = session.book

            print_info("Owner = {}".format(self.tx_coll[OWNER]), GREEN)
            self.report_info = InvestmentRecord(self.tx_coll[OWNER])

            self.create_gnucash_info()

            if self.mode == PROD:
                msg = "Mode = {}: COMMIT Price DB edits and Save session.".format(self.mode)
                print_info(msg, GREEN)
                self.price_db.commit_edit()
                # only ONE session save for the entire run
                session.save()

            session.end()
            session.destroy()

        except Exception as e:
            msg = "prepare_session() EXCEPTION!! '{}'".format(repr(e))
            print_error(msg)
            if "session" in locals() and session is not None:
                session.end()
                session.destroy()
            raise

        return msg


def create_gnc_txs_main(args):
    usage = "usage: py36 createGnucashTxs.py <monarch JSON file> <gnucash file> <mode: prod|test>"
    if len(args) < 3:
        print_error("NOT ENOUGH parameters!")
        print_info(usage, MAGENTA)
        exit(524)

    mon_file = args[0]
    if not osp.isfile(mon_file):
        print_error("File path '{}' does not exist. Exiting...".format(mon_file))
        print_info(usage, GREEN)
        exit(530)
    print_info("\nMonarch file = {}".format(mon_file), GREEN)

    # get Monarch transactions from the Monarch json file
    with open(mon_file, 'r') as fp:
        tx_coll = json.load(fp)

    gnc_file = args[1]
    if not osp.isfile(gnc_file):
        print_error("File path '{}' does not exist. Exiting...".format(gnc_file))
        exit(540)
    print_info("\nGnucash file = {}".format(gnc_file), GREEN)

    mode = args[2].upper()

    global strnow
    strnow = dt.now().strftime(DATE_STR_FORMAT)

    gtc = GncTxCreator(tx_coll, gnc_file, mode)
    msg = gtc.prepare_session()

    print_info("\n >>> PROGRAM ENDED.", MAGENTA)
    return msg


if __name__ == '__main__':
    import sys
    create_gnc_txs_main(sys.argv[1:])
