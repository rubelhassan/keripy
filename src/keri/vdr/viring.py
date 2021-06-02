# -*- encoding: utf-8 -*-
"""
keri.db.viring module

VIR  Verifiable Issuance(Revocation) Registry

Provides public simple Verifiable Credential Issuance/Revocation Registry
A special purpose Verifiable Data Registry (VDR)
"""

import traceback

from keri import kering
from keri.db import dbing
from keri.core import coring


def openReg(name="test", **kwa):
    """
    Returns contextmanager generated by openLMDB but with Baser instance
    """
    return dbing.openLMDB(cls=Registry, name=name, **kwa)


class Registry(dbing.LMDBer):
    """
    Issuer sets up named sub databases for VIR

    Attributes:
        see superclass LMDBer for inherited attributes


        .tvts is named sub DB whose values are serialized TEL events
            dgKey
            DB is keyed by identifer prefix plus digest of serialized event
            Only one value per DB key is allowed
        .tels is named sub DB of transaction event log tables that map sequence
            numbers to serialized event digests.
            snKey
            Values are digests used to lookup event in .tvts sub DB
            DB is keyed by identifer prefix plus sequence number of tel event
            Only one value per DB key is allowed
        .tibs is named sub DB of indexed backer signatures of event
            Backers always have nontransferable indetifier prefixes.
            The index is the offset of the backer into the backer list
            of the anchored management event wrt the receipted event.
            dgKey
            DB is keyed by identifer prefix plus digest of serialized event
            More than one value per DB key is allowed
        .oots is named sub DB of out of order escrowed event tables
            that map sequence numbers to serialized event digests.
            snKey
            Values are digests used to lookup event in .tvts sub DB
            DB is keyed by identifer prefix plus sequence number of key event
            Only one value per DB key is allowed
        .baks is named sub DB of ordered list of backers at given point in
            management TEL.
            dgKey
            DB is keyed by identifer prefix plus digest of serialized event
            More than one value per DB key is allowed
        .twes is named sub DB of partially witnessed escrowed event tables
            that map sequence numbers to serialized event digests.
            snKey
            Values are digests used to lookup event in .tvts sub DB
            DB is keyed by identifer prefix plus sequence number of tel event
            Only one value per DB key is allowed
        .taes is named sub DB of anchorless escrowed event tables
            that map sequence numbers to serialized event digests.
            snKey
            Values are digests used to lookup event in .tvts sub DB
            DB is keyed by identifer prefix plus sequence number of tel event
            Only one value per DB key is allowed
        .ancs is a named sub DB of anchors to KEL events.  Quadlet
            Each quadruple is concatenation of  four fully qualified items
            of validator. These are: transferable prefix, plus latest establishment
            event sequence number plus latest establishment event digest,
            plus indexed event signature.
            When latest establishment event is multisig then there will
            be multiple quadruples one per signing key, each a dup at same db key.
            dgKey
            DB is keyed by identifer prefix plus digest of serialized event
            Only one value per DB key is allowed

    Properties:


    """
    TailDirPath = "keri/reg"
    AltTailDirPath = ".keri/reg"
    TempPrefix = "keri_reg_"

    def __init__(self, headDirPath=None, reopen=True, **kwa):
        """
        Setup named sub databases.

        Inherited Parameters:
            name is str directory path name differentiator for main database
                When system employs more than one keri database, name allows
                differentiating each instance by name
            temp is boolean, assign to .temp
                True then open in temporary directory, clear on close
                Othewise then open persistent directory, do not clear on close
            headDirPath is optional str head directory pathname for main database
                If not provided use default .HeadDirpath
            mode is int numeric os dir permissions for database directory
            reopen is boolean, IF True then database will be reopened by this init

        Notes:

        dupsort=True for sub DB means allow unique (key,pair) duplicates at a key.
        Duplicate means that is more than one value at a key but not a redundant
        copies a (key,value) pair per key. In other words the pair (key,value)
        must be unique both key and value in combination.
        Attempting to put the same (key,value) pair a second time does
        not add another copy.

        Duplicates are inserted in lexocographic order by value, insertion order.

        """
        super(Registry, self).__init__(headDirPath=headDirPath, reopen=reopen, **kwa)

    def reopen(self, **kwa):
        """
        Open sub databases
        """
        super(Registry, self).reopen(**kwa)

        # Create by opening first time named sub DBs within main DB instance
        # Names end with "." as sub DB name must include a non Base64 character
        # to avoid namespace collisions with Base64 identifier prefixes.

        self.tvts = self.env.open_db(key=b'tvts.')
        self.tels = self.env.open_db(key=b'tels.')
        self.ancs = self.env.open_db(key=b'ancs.')
        self.tibs = self.env.open_db(key=b'tibs.', dupsort=True)
        self.baks = self.env.open_db(key=b'baks.', dupsort=True)
        self.oots = self.env.open_db(key=b'oots.')
        self.twes = self.env.open_db(key=b'twes.')
        self.taes = self.env.open_db(key=b'taes.')

        return self.env


    def clonePreIter(self, pre, fn=0):
        """
        Returns iterator of first seen event messages with attachments for the
        TEL prefix pre starting at first seen order number, fn.
        Essentially a replay in first seen order with attachments
        """
        if hasattr(pre, 'encode'):
            pre = pre.encode("utf-8")

        for fn, dig in self.getTelItemPreIter(pre, fn=fn):
            msg = bytearray()  # message
            atc = bytearray()  # attachments
            dgkey = dbing.dgKey(pre, dig) # get message
            if not (raw := self.getTvt(key=dgkey)):
                raise kering.MissingEntryError("Missing event for dig={}.".format(dig))
            msg.extend(raw)

            # add indexed backer signatures to attachments
            if (tibs := self.getTibs(key=dgkey)):
                atc.extend(coring.Counter(code=coring.CtrDex.WitnessIdxSigs,
                                                  count=len(tibs) ).qb64b)
                for tib in tibs:
                    atc.extend(tib)

            # add authorizer (delegator/issure) source seal event couple to attachments
            couple = self.getAnc(dgkey)
            if couple is not None:
                atc.extend(coring.Counter(code=coring.CtrDex.SealSourceCouples,
                                      count=1 ).qb64b)
                atc.extend(couple)

            # prepend pipelining counter to attachments
            if len(atc) % 4:
                raise ValueError("Invalid attachments size={}, nonintegral"
                                 " quadlets.".format(len(atc)))
            pcnt = coring.Counter(code=coring.CtrDex.AttachedMaterialQuadlets,
                                      count=(len(atc) // 4)).qb64b
            msg.extend(pcnt)
            msg.extend(atc)
            yield msg


    def putTvt(self, key, val):
        """
        Use dgKey()
        Write serialized VC bytes val to key
        Does not overwrite existing val if any
        Returns True If val successfully written Else False
        Return False if key already exists
        """
        return self.putVal(self.tvts, key, val)

    def setTvt(self, key, val):
        """
        Use dgKey()
        Write serialized VC bytes val to key
        Overwrites existing val if any
        Returns True If val successfully written Else False
        """
        return self.setVal(self.tvts, key, val)

    def getTvt(self, key):
        """
        Use dgKey()
        Return event at key
        Returns None if no entry at key
        """
        return self.getVal(self.tvts, key)

    def delTvt(self, key):
        """
        Use dgKey()
        Deletes value at key.
        Returns True If key exists in database Else False
        """
        return self.delVal(self.tvts, key)

    def putTel(self, key, val):
        """
        Use snKey()
        Write serialized VC bytes val to key
        Does not overwrite existing val if any
        Returns True If val successfully written Else False
        Return False if key already exists
        """
        return self.putVal(self.tels, key, val)

    def setTel(self, key, val):
        """
        Use snKey()
        Write serialized VC bytes val to key
        Overwrites existing val if any
        Returns True If val successfully written Else False
        """
        return self.setVal(self.tels, key, val)

    def getTel(self, key):
        """
        Use snKey()
        Return event at key
        Returns None if no entry at key
        """
        return self.getVal(self.tels, key)

    def delTel(self, key):
        """
        Use snKey()
        Deletes value at key.
        Returns True If key exists in database Else False
        """
        return self.delVal(self.tels, key)

    def getTelItemPreIter(self, pre, fn=0):
        """
        Returns iterator of all (fn, dig) duples in first seen order for all events
        with same prefix, pre, in database. Items are sorted by fnKey(pre, fn)
        where fn is first seen order number int.
        Returns a First Seen Event Log TEL.
        Returned items are duples of (fn, dig): Where fn is first seen order
        number int and dig is event digest for lookup in .evts sub db.

        Raises StopIteration Error when empty.

        Parameters:
            pre is bytes of itdentifier prefix
            fn is int fn to resume replay. Earliset is fn=0
        """
        return self.getAllOrdItemPreIter(db=self.tels, pre=pre, on=fn)


    def cntTels(self, pre, fn=0):
        """
        Returns count of all (fn, dig)  for all events
        with same prefix, pre, in database.

        Parameters:
            pre is bytes of itdentifier prefix
            fn is int fn to resume replay. Earliset is fn=0
        """
        return self.cntValsAllPre(db=self.tels, pre=pre, on=fn)

    def getTibs(self, key):
        """
        Use dgKey()
        Return list of indexed witness signatures at key
        Returns empty list if no entry at key
        Duplicates are retrieved in lexocographic order not insertion order.
        """
        return self.getVals(self.tibs, key)

    def getTibsIter(self, key):
        """
        Use dgKey()
        Return iterator of indexed witness signatures at key
        Raises StopIteration Error when empty
        Duplicates are retrieved in lexocographic order not insertion order.
        """
        return self.getValsIter(self.tibs, key)

    def putTibs(self, key, vals):
        """
        Use dgKey()
        Write each entry from list of bytes indexed witness signatures vals to key
        Adds to existing signatures at key if any
        Returns True If no error
        Apparently always returns True (is this how .put works with dupsort=True)
        Duplicates are inserted in lexocographic order not insertion order.
        """
        return self.putVals(self.tibs, key, vals)

    def addTib(self, key, val):
        """
        Use dgKey()
        Add indexed witness signature val bytes as dup to key in db
        Adds to existing values at key if any
        Returns True if written else False if dup val already exists
        Duplicates are inserted in lexocographic order not insertion order.
        """
        return self.addVal(self.tibs, key, val)

    def cntTibs(self, key):
        """
        Use dgKey()
        Return count of indexed witness signatures at key
        Returns zero if no entry at key
        """
        return self.cntVals(self.tibs, key)

    def delTibs(self, key, val=b''):
        """
        Use dgKey()
        Deletes all values at key if val = b'' else deletes dup val = val.
        Returns True If key exists in database (or key, val if val not b'') Else False
        """
        return self.delVals(self.tibs, key, val)

    def putTwe(self, key, val):
        """
        Use snKey()
        Write serialized VC bytes val to key
        Does not overwrite existing val if any
        Returns True If val successfully written Else False
        Return False if key already exists
        """
        return self.putVal(self.twes, key, val)

    def setTwe(self, key, val):
        """
        Use snKey()
        Write serialized VC bytes val to key
        Overwrites existing val if any
        Returns True If val successfully written Else False
        """
        return self.setVal(self.twes, key, val)

    def getTwe(self, key):
        """
        Use snKey()
        Return event at key
        Returns None if no entry at key
        """
        return self.getVal(self.twes, key)

    def delTwe(self, key):
        """
        Use snKey()
        Deletes value at key.
        Returns True If key exists in database Else False
        """
        return self.delVal(self.twes, key)

    def putTae(self, key, val):
        """
        Use snKey()
        Write serialized VC bytes val to key
        Does not overwrite existing val if any
        Returns True If val successfully written Else False
        Return False if key already exists
        """
        return self.putVal(self.taes, key, val)

    def setTae(self, key, val):
        """
        Use snKey()
        Write serialized VC bytes val to key
        Overwrites existing val if any
        Returns True If val successfully written Else False
        """
        return self.setVal(self.taes, key, val)

    def getTae(self, key):
        """
        Use snKey()
        Return event at key
        Returns None if no entry at key
        """
        return self.getVal(self.taes, key)

    def delTae(self, key):
        """
        Use snKey()
        Deletes value at key.
        Returns True If key exists in database Else False
        """
        return self.delVal(self.taes, key)


    def putOot(self, key, val):
        """
        Use snKey()
        Write serialized VC bytes val to key
        Does not overwrite existing val if any
        Returns True If val successfully written Else False
        Return False if key already exists
        """
        return self.putVal(self.oots, key, val)

    def setOot(self, key, val):
        """
        Use snKey()
        Write serialized VC bytes val to key
        Overwrites existing val if any
        Returns True If val successfully written Else False
        """
        return self.setVal(self.oots, key, val)

    def getOot(self, key):
        """
        Use snKey()
        Return event at key
        Returns None if no entry at key
        """
        return self.getVal(self.oots, key)

    def delOot(self, key):
        """
        Use snKey()
        Deletes value at key.
        Returns True If key exists in database Else False
        """
        return self.delVal(self.oots, key)


    def putAnc(self, key, val):
        """
        Use dgKey()
        Write serialized VC bytes val to key
        Does not overwrite existing val if any
        Returns True If val successfully written Else False
        Return False if key already exists
        """
        return self.putVal(self.ancs, key, val)

    def setAnc(self, key, val):
        """
        Use dgKey()
        Write serialized VC bytes val to key
        Overwrites existing val if any
        Returns True If val successfully written Else False
        """
        return self.setVal(self.ancs, key, val)

    def getAnc(self, key):
        """
        Use dgKey()
        Return event at key
        Returns None if no entry at key
        """
        return self.getVal(self.ancs, key)

    def delAnc(self, key):
        """
        Use dgKey()
        Deletes value at key.
        Returns True If key exists in database Else False
        """
        return self.delVal(self.ancs, key)


    def putBaks(self, key, vals):
        """
        Use dgKey()
        Write each entry from list of bytes prefixes to key
        Adds to existing backers at key if any
        Returns True If at least one of vals is added as dup, False otherwise
        Duplicates are inserted in insertion order.
        """
        return self.putIoVals(self.baks, key, vals)


    def addBak(self, key, val):
        """
        Use dgKey()
        Add prefix val bytes as dup to key in db
        Adds to existing values at key if any
        Returns True If at least one of vals is added as dup, False otherwise
        Duplicates are inserted in insertion order.
        """
        return self.addIoVal(self.baks, key, val)


    def getBaks(self, key):
        """
        Use dgKey()
        Return list of backer prefixes at key
        Returns empty list if no entry at key
        Duplicates are retrieved in insertion order.
        """
        return self.getIoVals(self.baks, key)


    def getBaksIter(self, key):
        """
        Use dgKey()
        Return iterator of backer prefixes at key
        Raises StopIteration Error when empty
        Duplicates are retrieved in insertion order.
        """
        return self.getIoValsIter(self.baks, key)

    def cntBaks(self, key):
        """
        Use dgKey()
        Return count of backer prefixes at key
        Returns zero if no entry at key
        """
        return self.cntIoVals(self.baks, key)


    def delBaks(self, key):
        """
        Use dgKey()
        Deletes all values at key in db.
        Returns True If key exists in database Else False
        """
        return self.delIoVals(self.baks, key)


    def delBak(self, key, val):
        """
        Use dgKey()
        Deletes dup val at key in db.
        Returns True If dup at  exists in db Else False

        Parameters:
            key is bytes of key within sub db's keyspace
            val is dup val (does not include insertion ordering proem)
        """
        return self.delIoVal(self.baks, key, val)



def nsKey(comps):
    """
    Returns bytes namespaced key from concatenation of ':' with qualified Base64
    prefix bytes components
    If any component is a str then converts to bytes
    """
    comps = map(lambda p: p if not hasattr(p, "encode") else p.encode("utf-8"), comps)
    return b':'.join(comps)

