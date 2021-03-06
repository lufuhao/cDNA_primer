"""Define class `Classifier` and `ClassifierException`."""
import os, sys
import os.path as op
import math
import re
import logging
import multiprocessing
from collections import defaultdict, namedtuple
from pbcore.util.Process import backticks
from pbcore.io.FastaIO import FastaReader, FastaWriter
from pbtools.pbtranscript.PBTranscriptException import PBTranscriptException
from pbtools.pbtranscript.io.DOMIO import DOMReader
from pbtools.pbtranscript.io.ReadAnnotation import ReadAnnotation
from pbtools.pbtranscript.io.Summary import ClassifySummary
from pbtools.pbtranscript.Utils import revcmp, realpath, \
    generateChunkedFN, cat_files


PBMATRIXFN = "PBMATRIX.txt"
PRIMERFN = "primers.fa"
PRIMERFRONTENDFN = "primers.front_end.fa"
PRIMERCHIMERAFN = "primers.chimera.fa"
PRIMERREPORTFN = "primer_info.csv"
FRONTENDDOMFN = "hmmer.front_end.dom"
CHIMERADOMFN = "hmmer.chimera.dom"
CLASSIFYSUMMARY = "classify_summary.txt"


# ChimeraDetectionOptions:
# Minimum length to output a (trimmed) sequence.
# Minimum phmmer score for primer hit.
# Minimum distance a primer has to be from end of sequence.
# Maximum distance between adjacent primer hits to consider as chimera.
# Search primers within windows of length primer_search_window.
ChimeraDetectionOptions = namedtuple("ChimeraDetectionOptions",
    ("min_seq_len min_score min_dist_from_end max_adjacent_hit_dist " +
     "primer_search_window"))


class PBRead(object):
    """Class for PacBio read."""
    def __init__(self, read):
        self.sequence = read.sequence
        self.name = read.name
        self.isCCS = False
        self.start, self.end = None, None
        self.movie, self.zmw = None, None

        #pattern: m....../1123/ccs
        m = re.search(r"(.+)/(\d+)/ccs", self.name)
        if m is None:
            #pattern: m...../1123 (alternative ccs)
            m = re.search(r"(.+)/(\d+)$", self.name)

        if m is not None:
            self.isCCS = True
            self.movie, self.zmw = m.groups()[0], int(m.groups()[1])
            self.start, self.end = 0, len(self.sequence)
        else: #pattern: m...../1123/23_450
            m = re.search(r"(.+)/(\d+)/(\d+)_(\d+)", self.name)
            if m is not None:
                self.movie = m.groups()[0]
                self.zmw, self.start, self.end = \
                    [int(x) for x in m.groups()[1:]]
            else:
                raise ValueError("Unsupported PacBio read {r}".
                                 format(r=self.name))


class ClassifierException(PBTranscriptException):
    """
    Exception class for Classifier.
    """
    def __init__(self, msg):
        PBTranscriptException.__init__(self, "classify", msg)


class Classifier(object):
    """
    Class for classifying reads based on whether they are full length and
    have their 5' primer, 3' primer and poly A tail seen, trim primers and
    PolyA tails from reads, and finally determine whether the trimmed reads
    are chimeras.
    """
    def __init__(self, reads_fn="test.fa", out_dir="output/",
                 out_reads_fn="testout.fa", primer_fn=None,
                 primer_report_fn=None, summary_fn=None,
                 cpus=1, change_read_id=True,
                 opts=ChimeraDetectionOptions(50, 10, 100, 50, 100),
                 out_nfl_fn=None, out_flnc_fn=None, ignore_polyA=False):
        self.reads_fn = realpath(reads_fn)
        self.out_dir = realpath(out_dir)
        self.cpus = cpus
        self.change_read_id = change_read_id
        self.chimera_detection_opts = opts
        self.ignore_polyA = ignore_polyA

        # The input primer file: primers.fa
        self.primer_fn = primer_fn if primer_fn is not None else \
            op.join(self.data_dir, PRIMERFN)
        # The output fasta file.
        self.out_all_reads_fn = realpath(out_reads_fn)

        # Intermediate output fasta file before chimera detection.
        # trimmed full-length reads before chimera detection
        self._trimmed_fl_reads_fn = op.join(self.out_dir, "fl.trimmed.fasta")

        self.primer_front_back_fn = op.join(self.out_dir, PRIMERFRONTENDFN)
        self.primer_chimera_fn = op.join(self.out_dir, PRIMERCHIMERAFN)

        # The output primer file: primer_info.csv
        self.primer_report_fn = primer_report_fn \
            if primer_report_fn is not None else \
            ".".join(out_reads_fn.split('.')[:-1]) + "." + PRIMERREPORTFN
        # primer reports for nfl reads before chimera detection. Note that
        # chimera detection is not necessary for nfl reads.
        self._primer_report_nfl_fn = op.join(self.out_dir,
                                             "primer_report.nfl.csv")
        # primer reports for fl reads after chimera detection. Note that
        # chimera detection is required for fl reads.
        self._primer_report_fl_fn = op.join(self.out_dir,
                                            "primer_report.fl.csv")

        # The matrix file: PBMATRIX.txt
        self.pbmatrix_fn = op.join(self.data_dir, PBMATRIXFN)

        # The output phmmer Dom file: hmmer.front_end.dom and hmmer.chimera.dom
        self.out_front_back_dom_fn = op.join(self.out_dir, FRONTENDDOMFN)
        self.out_trimmed_reads_dom_fn = op.join(self.out_dir, CHIMERADOMFN)

        self.chunked_front_back_reads_fns = None
        self.chunked_front_back_dom_fns = None

        self.chunked_trimmed_reads_fns = None
        self.chunked_trimmed_reads_dom_fns = None

        # The summary file: *.classify_summary.txt
        self.summary = ClassifySummary()
        self.summary_fn = summary_fn if summary_fn is not None else \
                          ".".join(out_reads_fn.split('.')[:-1]) + \
                          "." + CLASSIFYSUMMARY

        self.out_nfl_fn = realpath(out_nfl_fn) if out_nfl_fn is not None \
                          else op.join(self.out_dir, "nfl.fasta")
        self.out_flnc_fn = realpath(out_flnc_fn) if out_flnc_fn is not None \
                           else op.join(self.out_dir, "flnc.fasta")
        self.out_flc_fn = op.join(self.out_dir, "flc.fasta")

    def __str__(self):
        return ("reads_fn={0}\n".format(self.reads_fn) +
                "primer_fn={0}\n".format(self.primer_fn) +
                "out_all_reads_fn={0}\n".format(self.out_all_reads_fn) +
                "pbmatrix_fn={0}\n".format(self.pbmatrix_fn) +
                "out_front_back_dom_fn={0}\n".
                    format(self.out_front_back_dom_fn))

    @property
    def data_dir(self):
        """Return the data dir which has primers.fa and PBMATRIX.txt."""
        return op.join(op.dirname(op.realpath(__file__)), "data")

    def _validateInputs(self, reads_fn, primer_fn, pbmatrix_fn):
        """Validate whether input files and required data files all exist."""
        logging.info("Checking input files.")
        if not op.exists(reads_fn):
            raise ClassifierException(
                "Unable to find reads file: {fn}".format(fn=reads_fn))
        if not op.exists(primer_fn):
            raise ClassifierException(
                "Unable to find primer file: {fn}".format(fn=primer_fn))
        if not op.exists(pbmatrix_fn):
            raise ClassifierException(
                "Unable to find matrix file for PacBio reads: {fn}".
                format(fn=pbmatrix_fn))

    def _checkPhmmer(self):
        """Check phmmer can be called successfully."""
        logging.info("checking for phmmer existence.")
        _output, errCode, errMsg = backticks("phmmer -h > /dev/null")
        if errCode != 0:
            raise ClassifierException("Unable to invoke phmmer.\n{e}".
                format(e=errMsg))

    def _processPrimers(self, primer_fn, window_size, primer_out_fn,
                        revcmp_primers=False):
        """
        Check and generate primers.
        1. Check primers in primer_fn are in order F0, R0, F1, R1, ...
        Fn, Rn, and lengths are all < k, where k is the primer search
        window length.
           F0  5' NNNNNNNNNN 3'
           R0  3' NNNNNNNNNN 5'
        2. If Ri and Fi are revers complementarily identical,
        add a polyA tail to 3' of Ri.
        3. For each combo of primers Fi and Ri, save the following to
        primer_out_fn.
           3.1 If revcmp_primers is False,
              >Fi
              Fi_sequence
              >Ri
              revcmp(Ri_sequence)
           3.2 If revcmp_primers is True,
              >Fi
              Fi_sequence
              >Ri
              Ri_sequence
              >Fi_revcmp
              revcmp(Fi_sqeuence)
              >Ri_revcmp
              revcmp(Ri_sqeuence)
        4. return primers range(0, n)
        """
        logging.info("Process primers for {case}.".
                     format(case=("finding primers" if not revcmp_primers
                                  else "detecting chimeras")))
        freader = FastaReader(primer_fn)
        primers = []
        primerComboId = -1
        for i, r in enumerate(freader):
            if i % 2 == 0:
                direction = "F"
                primerComboId += 1
            else:
                direction = "R"
            expectedName = "{d}{n}".format(d=direction, n=primerComboId)

            if r.name != expectedName:
                errMsg = "Primers should be placed in order F0, R0, F1, R1..."
                logging.error(errMsg)
                raise ClassifierException(errMsg)

            if len(r.sequence) > window_size:
                errMsg = "Primer {n} has length {l} which is longer than {k}.".\
                    format(n=expectedName, l=len(r.sequence), k=window_size)
                logging.error(errMsg)
                raise ClassifierException(errMsg)

            if direction == "F":
                # Save >Fi and Fi_sequence.
                primers.append([expectedName, r.sequence])
            else:  # direction is "R"
                # fwdF/fwdR is the forward sequence of Fi/Ri
                fwdF, fwdR = primers[-1][1], r.sequence
                # revcmpF/revcmpR is the reverse complement of Fi/Ri
                revcmpF, revcmpR = revcmp(fwdF), revcmp(fwdR)
                # If Fi and Ri are reverse complementariliy identical, bail out,
                # because we need Poly A tail to distinguish Fi and Ri.
                if fwdF.find(revcmpR) >= 0 or revcmpR.find(fwdF) >= 0:
                    infoMsg = "Primer F{n}, R{n} ".format(n=primerComboId) + \
                        "are reverse complementarily identical. " + \
                        "Need to add 'AAAA' to 3' to distinguish them."
                    logging.info(infoMsg)
                    if revcmp_primers is False:
                        # Save primer Ri and revcmp(Ri_sequence) + TTTT
                        primers.append([expectedName, revcmpR + "T" * 4])
                    else:  # revcmp_primers is True
                        primers.append([expectedName, "A" * 4 + fwdR])
                        primers.append(['F{n}_revcmp'.format(n=primerComboId),
                                        revcmpF])
                        primers.append(['R{n}_revcmp'.format(n=primerComboId),
                                        revcmpR + "T" * 4])
                else:  # Ri and Fi are not revcmp identical
                    if revcmp_primers is False:
                        # Save >Ri and revcmp(Ri_sequence)
                        primers.append([expectedName, revcmpR])
                    else:
                        # Save >Ri and Ri_sequence
                        primers.append([expectedName, fwdR])
                        # Save >Fi_revcmp and revcmp(Fi_sequence)
                        primers.append(['F{n}_revcmp'.format(n=primerComboId),
                                        revcmpF])
                        # Save >Ri_revcmp and revcmp(Ri_sequence)
                        primers.append(['R{n}_revcmp'.format(n=primerComboId),
                                        revcmpR])
        freader.close()

        # Write Fi and reverse-complemented Ri to primer_out_fn
        f = open(primer_out_fn, 'w')
        for (name, seq) in primers:
            f.write(">{n}\n{s}\n".format(n=name, s=seq))
        f.close()
        return range(0, primerComboId + 1)

    @property
    def numReads(self):
        """Return the number of reads in reads_fn."""
        cmd = "grep -c '>' {r}".format(r=self.reads_fn)
        output, errCode, errMsg = backticks(cmd)
        if errCode != 0:
            raise ClassifierException(
                "Error reading file {r}:{e}".
                format(r=self.reads_fn, e=str(errMsg)))
        return int(output[0])

    def _chunkReads(self, reads_fn, reads_per_chunk, chunked_reads_fns,
            extract_front_back_only=True, window_size=100):
        """Split reads within reads_fn into multiple chunks each containing
        at most 'reads_per_chunk' reads, save to files in 'chunked_reads_fns'.
        If extract_front_back_only is true, extract the first and the last
        'window_size' bases and save them as readname_front and readname_back.
        Otherwise, copy read names and sequences entirely.
        """
        logging.info("Split {f} into ".format(f=reads_fn) +
                     "{n} chunks, ".format(n=len(chunked_reads_fns)) +
                      "each containing at most {n} reads.".
                      format(n=reads_per_chunk))
        if extract_front_back_only:
            logging.debug("Extract exactly {k} bases from front" +
                          " and end of each read.".format(k=window_size))

        freader = FastaReader(reads_fn)
        chunkIndex = -1
        fwriter = None
        for i, read in enumerate(freader):
            if i % reads_per_chunk == 0:
                chunkIndex += 1
                if fwriter is not None:
                    fwriter.close()
                    fwriter = None
                fwriter = open(chunked_reads_fns[chunkIndex], 'w')
            rcseq = revcmp(read.sequence)
            if extract_front_back_only:
                fwriter.write(">{n}_front\n{s}\n>{n}_back\n{rcs}\n".format(
                              n=read.name, s=read.sequence[:window_size],
                              rcs=rcseq[:window_size]))
            else:
                fwriter.write(">{n}\n{s}\n".format(n=read.name,
                                                   s=read.sequence))

        if fwriter is not None:
            fwriter.close()


    def _startPhmmers(self, chunked_reads_fns, chunkedDomFNs, outDomFN,
            primer_fn, pbmatrix_fn):
        """Run phmmers on chunked reads files in 'chunked_reads_fns' and
        generate chunked dom files as listed in 'chunkedDomFNs', finally
        concatenate dom files to 'outDomFN'."""
        logging.info("Start to launch phmmer on chunked reads.")
        jobs = []
        for reads_fn, domFN in zip(chunked_reads_fns, chunkedDomFNs):
            p = multiprocessing.Process(
                target=self._phmmer,
                args=(reads_fn, domFN, primer_fn, pbmatrix_fn))
            jobs.append((p, domFN))
            p.start()

        for p, domFN in jobs:
            p.join()
            cmd = "cat {0} >> {1}".format(domFN, outDomFN)
            _output, errCode, errMsg = backticks(cmd)
            if errCode != 0:
                raise ClassifierException(
                    "Error concatenating dom files: {e}".
                    format(e=str(errMsg)))

    def _phmmer(self, reads_fn, domFN, primer_fn, pbmaxtrixFN):
        """Invoke phmmer once."""
        cmd = "phmmer --domtblout {d} --noali --domE 1 ".format(d=domFN) + \
              "--mxfile {m} ".format(m=pbmaxtrixFN) + \
              "--popen 0.07 --pextend 0.07 {r} {p} > /dev/null".\
              format(r=reads_fn, p=primer_fn)
        logging.debug("Calling phmmer: {cmd}".format(cmd=cmd))
        _output, errCode, errMsg = backticks(cmd)
        if (errCode != 0):
            raise ClassifierException(
                "Error calling phmmer: {e}.".format(e=str(errMsg)))

    def _getBestFrontBackRecord(self, domFN):
        """Parses DOM output from phmmer and fill in best_of_front, best_of_back
           bestOf: sequence id ---> DOMRecord
        """
        logging.info("Get the best front & back primer hits.")
        #bestOf_ = {} # key: sid --> primer name --> DOMRecord
        best_of_front = defaultdict(lambda:None)
        best_of_back = defaultdict(lambda:None)

        reader = DOMReader(domFN)
        for r in reader:
            # allow missing adapter
            if r.sStart > 48 or r.pStart > 48:
                continue

            if r.sid.endswith('_front'):# _front
                bestOf = best_of_front
                r.sid = r.sid[:-6]
            elif r.sid.endswith('_back'):# _back
                bestOf = best_of_back
                r.sid = r.sid[:-5]
            else:
                raise ClassifierException(
                    "Unable to parse a read {r} in phmmer dom file {f}.".
                    format(r=r.sid, f=domFN))
            if r.sid not in bestOf:
                bestOf[r.sid] = {}
            if (r.pid in bestOf[r.sid] and
                bestOf[r.sid][r.pid].score < r.score) or \
               (r.pid not in bestOf[r.sid]):
                bestOf[r.sid][r.pid] = r
        return (best_of_front, best_of_back)


    def _getChimeraRecord(self, domFN, opts):
        """Parses phmmer DOM output from trimmed reads for chimera
           detection, return DOMRecord of suspicious chimeras, which
           have primer hits in the MIDDLE of the sequence.
        """
        logging.info("Identify chimera records.")
        # sid --> list of DOMRecord with primer hits in the middle
        # of sequence.
        suspicous_hits = defaultdict(lambda:[])
        reader = DOMReader(domFN)
        for r in reader:
            # A hit has to be in the middle of sequence, and with
            # decent score.
            if r.sStart > opts.min_dist_from_end and \
               r.sEnd < r.sLen - opts.min_dist_from_end and \
               r.score > opts.min_score:
                suspicous_hits[r.sid].append(r)
        return suspicous_hits

    def _updateChimeraInfo(self, suspicous_hits, in_read_fn, out_flnc_fn,
                           out_flc_fn, primer_report_fl_fn):
        """
        in_read_fn --- a fasta of full-length reads
        For each full-length read in in_read_fn FASTA file, detect whether
        it is chimeric or not, and write its annotation to
        primer_report_fl_fn.
        """
        logging.info("Update chimera info to reads annotations " +
                     "in the output FASTA file and the primer report.")

        with FastaReader(in_read_fn) as reader, \
             FastaWriter(out_flnc_fn) as writer, \
             FastaWriter(out_flc_fn) as writer_chimera, \
             open(primer_report_fl_fn, 'w') as reporter:
            reporter.write("\t".join(ReadAnnotation.fieldsNames()) + "\n")
            for r in reader:
                # e.g. r.name="movie/zmw/0_100_CCS fiveend=1;threeend=100;"
                readid = r.name.split()[0]
                annotation = ReadAnnotation.fromString(r.name, ignore_polyA=self.ignore_polyA)
                if readid not in suspicous_hits:  # Non-chimeric reads
                    # Primer of a primer-trimmed read can not be None.
                    # assert(annotation.primer is not None)
                    annotation.chimera = 0
                    assert(annotation.isFullLength)
                    self.summary.num_flnc += 1
                    self.summary.num_flnc_bases += len(r.sequence)
                    writer.writeRecord(annotation.toAnnotation(),
                                       r.sequence)
                else:  # chimeric reads
                    annotation.chimera = 1
                    self.summary.num_flc += 1
                    writer_chimera.writeRecord(annotation.toAnnotation(),
                                               r.sequence)

                reporter.write(annotation.toReportRecord() + "\n")


    def _findPolyA(self, seq, min_a_num=8, three_start=None):
        """
        Find poly A tail, which has at least 'min_a_num' A bases and at most
        two non-A bases in 3' of sequence. Return index of the very first base,
        if a polyA tail is found; otherwise, return -1.
        """
        polyA = 'A' * min_a_num
        offset = 50
        startEnd = three_start - offset if three_start is not None \
                   else len(seq) - offset
        # search within the last <offset> bp
        i = seq.rfind(polyA, startEnd)
        if i > 0:
            nonA = 0
            # backtrace to the front of polyA, allowing only 2 max non-A
            while i >= 0:
                nonA += (seq[i] != 'A')
                if nonA > 2:
                    break
                i -= 1
            return i+1
        else:
            return -1

    def _pickBestPrimerCombo(self, dFront, dBack, primer_indices, min_score):
        """Pick up best primer combo.

        best_of_front/Back: {read_id: {primer_name:DOMRecord}}
        If the read is '+' strand: then front -> F0, back -> R0
        else: front -> R0, back -> F0
        Returns: primer index, left_DOMRecord or None, right_DOMRecord or None
        """
        def getDomRecord(d, k, min_score):
            """d: {k:DomRecord}
            Return DomRecord if d[k].score > min_score; otherwise return None.
            """
            if d is not None and k in d and d[k].score >= min_score:
                return d[k]
            else:
                return None
        logging.debug("dFront={0}".format(dFront))
        logging.debug("dBack={0}".format(dBack))
        tally = {}
        for ind in primer_indices:
            fpid, rpid = 'F' + str(ind), 'R' + str(ind)
            tally[(ind, '+')] = 0
            if dFront is not None and fpid in dFront:
                tally[(ind, '+')] += dFront[fpid].score
            if dBack is not None and rpid in dBack:
                tally[(ind, '+')] += dBack[rpid].score
            tally[(ind, '-')] = 0
            if dFront is not None and rpid in dFront:
                tally[(ind, '-')] += dFront[rpid].score
            if dBack is not None and fpid in dBack:
                tally[(ind, '-')] += dBack[fpid].score

        bestInd, bestStrand, bestScore = (None, None, -1000)
        for idStrand, s in tally.iteritems():
            if bestScore <= s:
                bestScore = s
                bestInd, bestStrand = idStrand

        k1 = 'F' + str(bestInd)
        k2 = 'R' + str(bestInd)
        if bestStrand == '+':
            return (bestInd, bestStrand,
                    getDomRecord(dFront, k1, min_score),
                    getDomRecord(dBack, k2, min_score))
        else:
            return (bestInd, bestStrand,
                    getDomRecord(dBack, k1, min_score),
                    getDomRecord(dFront, k2, min_score))

    def _trimBarCode(self, reads_fn, out_fl_reads_fn, out_nfl_reads_fn,
            primer_report_nfl_fn,
            best_of_front, best_of_back, primer_indices,
            min_seq_len, min_score, change_read_id, ignore_polyA):
        """Trim bar code from reads in 'reads_fn', annotate each read,
        indicating:
            whether its 5' primer, 3' primer and polyA tail are seen,
            start positions of its 5' primer, 3' primer and polyA tail,
            and primer info.
        , save non-full-length reads to 'out_nfl_reads_fn',
        , save full-length reads to 'out_fl_reads_fn', which can later be
        used in chimera detection
        , write primer info of nfl reads to _primer_report_nfl_fn.

        Note that chimera detection is not necessary for nfl reads, but
        is required for fl reads. So we only write primer info for nfl here
        and will write primer info for fl reads when chimera detection
        is done.

        best_of_front/Back: {read_id: {primer_name:DOMRecord}}
        min_seq_len: minimum length to output a read.
        min_score: minimum score to output a read.
        change_read_id: if True, change read ids to 'movie/zmw/start_end'.
        """
        logging.info("Trim bar code away from reads.")
        logging.debug("Writing full-length trimmed reads to {f}".
                      format(f=out_fl_reads_fn))
        logging.debug("Writing non-full-length trimmed reads to {f}".
                      format(f=out_nfl_reads_fn))
        logging.debug("Writing primer reports before chimera detection to {f}".
                      format(f=primer_report_nfl_fn))

        with FastaReader(reads_fn) as fareader, \
             FastaWriter(out_nfl_reads_fn) as nfl_fawriter, \
             FastaWriter(out_fl_reads_fn) as fl_fawriter, \
             open(primer_report_nfl_fn, 'w') as reporter:
            for read in fareader:
                self.summary.num_reads += 1  # number of ROI reads
                pbread = PBRead(read)
                logging.debug("Pick up best primer combo for {r}".
                              format(r=read.name))
                primerIndex, strand, fw, rc = self._pickBestPrimerCombo(
                    best_of_front[read.name], best_of_back[read.name],
                    primer_indices, min_score)
                logging.debug("read={0}\n".format(read.name) +
                        "primer={0} strand={1} fw={2} rc={3}".
                        format(primerIndex, strand, fw, rc))

                if fw is None and rc is None:
                    # No primer seen in this sequence, classified
                    # as non-full-length
                    newName = pbread.name
                    if change_read_id:
                        newName = "{m}/{z}/{s1}_{e1}{isccs}".format(
                                  m=pbread.movie, z=pbread.zmw,
                                  s1=pbread.start, e1=pbread.end,
                                  isccs=("_CCS" if pbread.isCCS else ""))
                    annotation = ReadAnnotation(ID=newName)
                    # Write reports of nfl reads
                    reporter.write(annotation.toReportRecord() + "\n")
                    if len(read.sequence) >= min_seq_len:
                        # output non-full-length reads to nfl.trimmed.fasta
                        nfl_fawriter.writeRecord(annotation.toAnnotation(),
                                                 read.sequence)
                        self.summary.num_nfl += 1
                    else:
                        self.summary.num_filtered_short_reads += 1
                    continue
                seq = read.sequence if strand == "+" else revcmp(read.sequence)
                five_end, three_start = None, None
                if fw is not None:
                    five_end = fw.sEnd
                    self.summary.num_5_seen += 1
                if rc is not None:
                    three_start = len(seq) - rc.sEnd
                    self.summary.num_3_seen += 1

                s, e = pbread.start, pbread.end
                # Try to find polyA tail in read
                polyAPos = self._findPolyA(seq, three_start=three_start)
                if polyAPos >= 0: # polyA found
                    seq = seq[:polyAPos]
                    e1 = s + polyAPos if strand == "+" else e - polyAPos
                    self.summary.num_polyA_seen += 1
                elif three_start is not None: # polyA not found
                    seq = seq[:three_start]
                    e1 = s + three_start if strand == "+" else e - three_start
                else:
                    e1 = e if strand == "+" else s

                if five_end is not None:
                    seq = seq[five_end:]
                    s1 = s + five_end if strand == "+" else e - five_end
                else:
                    s1 = s if strand == "+" else e

                newName = pbread.name
                if change_read_id:
                    newName = "{m}/{z}/{s1}_{e1}{isccs}".format(
                        m=pbread.movie, z=pbread.zmw, s1=s1, e1=e1,
                        isccs=("_CCS" if pbread.isCCS else ""))
                # Create an annotation
                annotation = ReadAnnotation(ID=newName, strand=strand,
                    fiveend=five_end, polyAend=polyAPos,
                    threeend=three_start, primer=primerIndex, ignore_polyA=ignore_polyA)

                # Write reports for nfl reads
                if annotation.isFullLength is not True:
                    reporter.write(annotation.toReportRecord() + "\n")

                if len(seq) >= min_seq_len:
                    if annotation.isFullLength is True:
                        # Write long full-length reads
                        fl_fawriter.writeRecord(annotation.toAnnotation(), seq)
                        self.summary.num_fl += 1
                    else:
                        # Write long non-full-length reads.
                        nfl_fawriter.writeRecord(annotation.toAnnotation(), seq)
                        self.summary.num_nfl += 1
                else:
                    self.summary.num_filtered_short_reads += 1

    def _validateOutputs(self, out_dir, out_all_reads_fn):
        """Validate and create output directory."""
        logging.info("Creating output directory {d}.".format(d=out_dir))
        if op.exists(out_dir):
            logging.info("Warning: Output directory {d} already exists.".
                         format(d=out_dir))
        else:
            os.mkdir(out_dir)
        if op.exists(out_all_reads_fn):
            logging.info("Warning: Output file {f} already exists.".format(f=out_all_reads_fn))
#            raise ClassifierException(
#                "Output file {f} already exists.".format(f=out_all_reads_fn))

    def _cleanup(self, fileList):
        """Remove files in the list if they exist."""
        logging.debug("Clean up intermediate files.")
        for f in fileList:
            if op.exists(f):
                os.remove(f)

    def runPrimerTrimmer(self):
        """Run PHMMER to identify barcodes and trim them away.
        (1) create forward/reverse primers
        (2) copy input with just the first/last k bases
        (3) run phmmer
        (4) parse phmmer DOM output, trim barcodes and output summary
        """
        logging.info("Start to find and trim 3'/5' primers and polyAs.")
        # Sanity check input primers and create forward/reverse primers
        # for primer detection.
        primer_indices = self._processPrimers(
            primer_fn=self.primer_fn,
            window_size=self.chimera_detection_opts.primer_search_window,
            primer_out_fn=self.primer_front_back_fn,
            revcmp_primers=False)

        need_cleanup = True
        if os.path.exists(self.out_front_back_dom_fn):
            logging.info("Output already exists. Parsing {0}".format(self.out_front_back_dom_fn))
            need_cleanup = False
        else:
            # Split reads in reads_fn into smaller chunks.
            num_chunks = max(min(self.cpus, self.numReads), 1)
            reads_per_chunk = int(math.ceil(self.numReads/(float(num_chunks))))
            num_chunks = int(math.ceil(self.numReads/float(reads_per_chunk)))
    
            logging.debug("Split all reads into {n} chunks".format(n=num_chunks))
    
            # Divide input reads into smaller chunks and extract only
            # the front and the end segment from each read.
            self.chunked_front_back_reads_fns = generateChunkedFN(self.out_dir,
                "in.front_end.fa_split", num_chunks)
    
            # Dom output of phmmer for the above front/end sequences.
            self.chunked_front_back_dom_fns = generateChunkedFN(self.out_dir,
                "out.front_end.hmmer_split", num_chunks)
    
            # Split reads within 'reads_fn' into 'num_chunks' chunks, and only
            # extract the front and end segment from each read.
            window_size = self.chimera_detection_opts.primer_search_window
            self._chunkReads(reads_fn=self.reads_fn,
                             reads_per_chunk=reads_per_chunk,
                             chunked_reads_fns=self.chunked_front_back_reads_fns,
                             extract_front_back_only=True,
                             window_size=window_size)
    
            # Start n='num_chunks' phmmer.
            self._startPhmmers(self.chunked_front_back_reads_fns,
                               self.chunked_front_back_dom_fns,
                               self.out_front_back_dom_fn,
                               self.primer_front_back_fn,
                               self.pbmatrix_fn)

        # Parse dome file, and return dictionary of front & back.
        best_of_front, best_of_back = self._getBestFrontBackRecord(
                self.out_front_back_dom_fn)

        # Trim bar code away
        self._trimBarCode(reads_fn=self.reads_fn,
                          out_fl_reads_fn=self._trimmed_fl_reads_fn,
                          out_nfl_reads_fn=self.out_nfl_fn,
                          primer_report_nfl_fn=self._primer_report_nfl_fn,
                          best_of_front=best_of_front,
                          best_of_back=best_of_back,
                          primer_indices=primer_indices,
                          min_seq_len=self.chimera_detection_opts.min_seq_len,
                          min_score=self.chimera_detection_opts.min_score,
                          change_read_id=self.change_read_id,
                          ignore_polyA=self.ignore_polyA)

        if need_cleanup:
            # Clean intemediate files: chunked reads files and chunked dom files.
            self._cleanup(self.chunked_front_back_reads_fns +
                          self.chunked_front_back_dom_fns)
        logging.info("Done with finding and trimming primers and polyAs.")

    def runChimeraDetector(self):
        """Detect chimeras from trimmed reads."""
        logging.info("Start to detect chimeras from trimmed reads.")
        need_cleanup = True
        if os.path.exists(self.out_trimmed_reads_dom_fn):
            logging.info("Output already exists. Parsing {0}.".format(self.out_trimmed_reads_dom_fn))
            need_cleanup = False
        else:
            # Create forward/reverse primers for chimera detection.
            _primer_indices = self._processPrimers(
                primer_fn=self.primer_fn,
                window_size=self.chimera_detection_opts.primer_search_window,
                primer_out_fn=self.primer_chimera_fn,
                revcmp_primers=True)
    
            num_chunks = max(min(self.summary.num_fl, self.cpus), 1)
            #logging.debug("Split non-full-length reads into {n} chunks.".
            #              format(n=num_chunks))
            # Only detect chimeras on full-length reads in order to save time
            reads_per_chunk = int(math.ceil(self.summary.num_fl /
                                            (float(num_chunks))))
            num_chunks = int(math.ceil(self.summary.num_fl/float(reads_per_chunk)))
    
            self.chunked_trimmed_reads_fns = generateChunkedFN(self.out_dir,
                "in.trimmed.fa_split", num_chunks)
    
            self.chunked_trimmed_reads_dom_fns = generateChunkedFN(self.out_dir,
                "out.trimmed.hmmer_split", num_chunks)
    
            self._chunkReads(reads_fn=self._trimmed_fl_reads_fn,
                             reads_per_chunk=reads_per_chunk,
                             chunked_reads_fns=self.chunked_trimmed_reads_fns,
                             extract_front_back_only=False)
    
            self._startPhmmers(self.chunked_trimmed_reads_fns,
                               self.chunked_trimmed_reads_dom_fns,
                               self.out_trimmed_reads_dom_fn,
                               self.primer_chimera_fn,
                               self.pbmatrix_fn)

        suspicous_hits = self._getChimeraRecord(self.out_trimmed_reads_dom_fn,
                                                self.chimera_detection_opts)

        # Only detect chimeras on full-length reads in order to save time
        self._updateChimeraInfo(suspicous_hits=suspicous_hits,
                                in_read_fn=self._trimmed_fl_reads_fn,
                                out_flnc_fn=self.out_flnc_fn,
                                out_flc_fn=self.out_flc_fn,
                                primer_report_fl_fn=self._primer_report_fl_fn)
        # full-length non-chimeric reads written to out_flnc.fa
        # non-full-length reads written to out_nfl.fa
        # primer info of fl reads reported to _primer_report_fl_fn
        # primer info of nfl reads reported to _primer_report_nfl_fn
        # Need to: (1) concatenate out_flnc and out_nfl to make
        #              out_all_reads_fn
        #          (2) concatenate _primer_report_fl_fn and
        #              _primer_report_nfl_fn to make primer_report_fn
        cat_files(src=[self.out_flnc_fn, self.out_nfl_fn],
                  dst=self.out_all_reads_fn)

        cat_files(src=[self._primer_report_fl_fn, self._primer_report_nfl_fn],
                  dst=self.primer_report_fn)

        if need_cleanup:
            self._cleanup(self.chunked_trimmed_reads_fns +
                          self.chunked_trimmed_reads_dom_fns)
        logging.info("Done with chimera detection.")

    def run(self):
        """Classify/annotate reads according to 5' primer seen,
        3' primer seen, polyA seen, chimera (concatenation of two
        or multiple transcripts with primers seen in the middle of
        a read)
        (1) Create and validate input/output
        (2) Check phmmer is runnable
        (3) Find primers using phmmer and trim away primers and polyAs
        (4) Detect chimeras from trimmed reads
        """
        # Validate input files and required data files.
        self._validateInputs(self.reads_fn, self.primer_fn, self.pbmatrix_fn)

        # Validate and create output dir.
        self._validateOutputs(self.out_dir, self.out_all_reads_fn)

        # Sanity check phmmer can be called successfully.
        self._checkPhmmer()

        # Find and trim primers and polyAs.
        self.runPrimerTrimmer()

        # Check whether no fl reads detected.
        no_flnc_errMsg = "No full-length non-chimeric reads detected."
        if self.summary.num_fl == 0:
            logging.error(no_flnc_errMsg)
            raise ClassifierException(no_flnc_errMsg)

        # Detect chimeras and generate primer reports.
        self.runChimeraDetector()

        try:
            # Write summary.
            self.summary.write(self.summary_fn)

            # Delete intermediate files.
            self._cleanup([self._primer_report_nfl_fn,
                           self._primer_report_fl_fn])
        except ZeroDivisionError:
            logging.error(no_flnc_errMsg)
            raise ClassifierException(no_flnc_errMsg)

        return 0


if __name__ == "__main__":
    obj = Classifier()
    obj.run()
