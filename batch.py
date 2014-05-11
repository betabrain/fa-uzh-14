import sqlite3
import leveldb
import time
import string
import os
import collections
import struct
import shutil
import itertools
import functools
import pprint
import sys
import resource
import types
import blessings
import codecs
import sh
from psutil import Process as P; P = P()

# config

if len(sys.argv) == 2:
    n_records = int(sys.argv[-1])
else:
    n_records = 500

bad_values = set(list(string.ascii_letters + string.digits))

time_started = time.clock()

stats = {
    'Records.N': n_records,
    # 'time_started': time_started
}

# helpers

def info(*args, **kwargs):
    print >>sys.stderr, 'ARGS', args
    print >>sys.stderr, 'KWARGS', kwargs

def get_du(p):
    if os.path.exists(p):
        return int(str(sh.du('-k', p)).split()[0]) * 1024
    else:
        return 0L

get_wdb = functools.partial(get_du, 'batch.sqlite3')
get_ldb = functools.partial(get_du, 'batch.leveldb')

class timer(object):
    def __init__(self, name='<block>'):
        self.name = name
        self.start_sys  = 0.0
        self.start_user = 0.0
        self.start_rss = 0L
        self.start_disk = 0L
    def __enter__(self):
        cput = P.cpu_times()
        memi = P.memory_info_ex()
        self.start_sys  = cput.system
        self.start_user = cput.user
        self.start_rss  = memi.rss
        self.start_disk = get_wdb() + get_ldb()
    def __exit__(self, *args):
        cput = P.cpu_times()
        memi = P.memory_info_ex()
        self.stop_sys  = cput.system
        self.stop_user = cput.user
        self.stop_rss  = memi.rss
        self.stop_disk = get_wdb() + get_ldb()
        t_elapsed_sys  = self.stop_sys - self.start_sys
        t_elapsed_user = self.stop_user - self.start_user
        t_elapsed = t_elapsed_sys + t_elapsed_user
        print >>sys.stderr, blessings.Terminal().yellow('timer: {} took {} (user: {}, sys: {}) seconds.'.format(self.name, t_elapsed, t_elapsed_user, t_elapsed_sys))
        print >>sys.stderr, blessings.Terminal().yellow('timer: rss = {} MiB. (change: {} MiB).'.format(self.stop_rss/1048576.0, (self.stop_rss-self.start_rss)/1048576.0))
        print >>sys.stderr, blessings.Terminal().yellow('timer: disk = {} MiB. (change: {} MiB).'.format(self.stop_disk/1048576.0, (self.stop_disk-self.start_disk)/1048576.0))
        print >>sys.stderr
        #stats[self.name+'.user']  = t_elapsed_user
        #stats[self.name+'.sys']   = t_elapsed_sys
        #stats[self.name+'.total'] = t_elapsed
        #stats[self.name+'.rss'] = self.stop_rss
        #stats[self.name+'.disk'] = self.stop_disk
        stats[self.name+'.Memory'] = self.stop_rss + self.stop_disk
        stats[self.name+'.Runtime'] = t_elapsed

def main():
    info('retry3.py started.')

    # opening connections to all databases and some necessary cleaning and setup.
    #  - db_s: data source
    #  - db_w: in memory working set
    #  - db_e: on disk leveldb hashtable
    #

    with timer('Setup'):
        db_s = sqlite3.connect('cleaned.sqlite3')
        cu_s = db_s.cursor()
        #db_w = sqlite3.connect(':memory:')
        if os.path.exists('batch.sqlite3'):
            os.remove('batch.sqlite3')
        db_w = sqlite3.connect('batch.sqlite3')
        cu_w = db_w.cursor()

        cu_w.execute('''
            CREATE TABLE profile (id integer not null, cluster integer not null, block integer not null);
        ''')
        db_w.commit()

        #cu_w.execute('''
        #    CREATE TABLE cluster (id INT, cluster INT)
        #''')
        #db_w.commit()

        if os.path.exists('batch.leveldb'):
            info('cleaning up old hashtable...')
            shutil.rmtree('batch.leveldb')

        megabyte = 1024**2
        db_e = leveldb.LevelDB('batch.leveldb', \
                               block_cache_size=128*megabyte, \
                               write_buffer_size=128*megabyte)

        info('databases ready.')

        #info('reading blocks...')

        # reading all data from the source database, preprocessing, and encoding.
        # this results in a table of (profile, block) associations.
        # Also extract the ground truth (profile, cluster) into another table for later.
        #
        #block_counts = collections.Counter()
        #block_ids    = dict()

        #not_none  = lambda v: v
        #clean_str = lambda v: unicode(v).strip()
        #not_empty = lambda v: len(v)

        #ok_chars  = string.ascii_letters + string.digits + '- '
        #sane_str  = lambda c: c in ok_chars

        #for record in cu_s.execute('SELECT id, cluster, name, sort_name, type, area, gender, comment, begin_year, end_year from artist_sample order by cluster limit {};'.format(n_records)):
        #    _id = int(record[0])
        #    _cl = int(record[1])

        #    tokens = filter(not_none, record[2:])
        #    tokens = map(clean_str, tokens)
        #    tokens = filter(not_empty, tokens)
        #    tokens = u' '.join(tokens)
        #    tokens = u''.join(filter(sane_str, tokens)).lower()
        #    tokens = tokens.split()
        #    tokens = set(tokens)

        #    for token in tokens:
        #        block_counts[token] += 1
        #        block_id = block_ids.get(token, None)
        #        if block_id == None:
        #            block_id = len(block_ids)
        #            block_ids[token] = block_id
        #        cu_w.execute('INSERT INTO profile (id, cluster, block) VALUES (?, ?, ?);', (_id, _cl, block_id))

        #db_w.commit()

        #cu_w.execute('CREATE INDEX iprofblock ON profile (block);')
        #db_w.commit()

        #with codecs.open('blocks.txt', 'w', 'utf-8') as fh:
        #    for value in block_ids:
        #        print >>fh, value, block_counts[value]

        #info('blocks extracted.')

        block_keys = {}
        block_to_value = {}
        clusters = collections.defaultdict(set)


        ok_chars  = string.ascii_letters + string.digits + ' '

        sane_str = lambda c: c in ok_chars

        for record in cu_s.execute('SELECT id, cluster, name, sort_name, type, area, gender, comment, begin_year, end_year FROM artist_sample ORDER BY cluster, id LIMIT {};'.format(n_records)):
            _id = int(record[0])
            _cl = int(record[1])

            clusters[_cl].add(_id)

            for value in record[2:]:
                if value:
                    value = unicode(value).strip()

                    if value:
                        values = u''.join(filter(sane_str, value)).lower().split()

                        for value in values:

                            if value in bad_values:
                                continue

                            block = block_keys.get(value, None)

                            if block == None:
                                block = len(block_keys)
                                block_keys[value] = block
                                block_to_value[block] = value

                            cu_w.execute('INSERT INTO profile (id, cluster, block) VALUES (?,?,?);', (_id, _cl, block))

        cu_s.close()
        db_s.close()
        del cu_s, db_s

        cu_w.execute('CREATE INDEX iprofblock ON profile (block);')
        db_w.commit()

        #n_associations = cu_w.execute('SELECT count(*) FROM profile;').fetchone()[0]
        #n_blocks = cu_w.execute('SELECT count(DISTINCT block) FROM profile;').fetchone()[0]
        #n_avg_assoc_per_block = float(n_associations) / n_blocks
        #info('number of associations retreived.', n_associations=n_associations, n_blocks=n_blocks, n_avg_assoc_per_block=n_avg_assoc_per_block)

        #info('removing bad blocks...')

        ## some blocks contain too many profiles to be computationally feasable,
        ## hence they need to be skipped.
        ## furthermore, skip all blocks with just one profile.
        ##
        def fak(n):
            return reduce(lambda x,y: x*y, xrange(1, n+1), 1)

        def combs(n, k):
            return fak(n)/fak(k)/fak(n-k)

        #n_min_profiles = 2
        #n_max_profiles = 1500 # why?

        # first, collect some statistics.
        #n_rare_profile_blocks = len(cu_w.execute('SELECT count(block) FROM profile GROUP BY block HAVING count(id) < {};'.format(n_min_profiles)).fetchall())
        #n_frequent_profile_blocks = len(cu_w.execute('SELECT count(block) FROM profile GROUP BY block HAVING count(id) > {};'.format(n_max_profiles)).fetchall())

        #info('collected bad blocks statistics', n_rare_profile_blocks=n_rare_profile_blocks, n_frequent_profile_blocks=n_frequent_profile_blocks)

        #cu_w.execute('''
        #    CREATE TABLE badblocks
        #        AS SELECT block, count(id) AS count FROM profile GROUP BY block
        #           HAVING count(id) < {} OR count(id) > {};
        #'''.format(n_min_profiles, n_max_profiles))
        #db_w.commit()

        #cu_w.execute('CREATE INDEX ibb ON badblocks (block);')
        #db_w.commit()

        #cu_w.execute('CREATE INDEX ibc ON badblocks (count);')
        #db_w.commit()

        #n_edges_skipped = 0L
        #n_bad_blocks = 0L

        #ids2value = dict(map(lambda (a, b): (b, a), block_ids.items()))

        #def mem(cnt):
        #    cnt *= (cnt - 1)
        #    cnt *= 8.0
        #    if cnt < 1024: return str(int(cnt)) + 'B'
        #    cnt /= 1024
        #    if cnt < 1024: return str(int(cnt)) + 'KB'
        #    cnt /= 1024
        #    if cnt < 1024: return str(int(cnt)) + 'MB'
        #    cnt /= 1024
        #    if cnt < 1024: return str(int(cnt)) + 'GB'
        #    cnt /= 1024
        #    return str(int(cnt)) + 'TB'


        #with codecs.open('badblocks.txt', 'w', 'utf-8') as fh:
        #    for block, count in cu_w.execute('SELECT block, count FROM badblocks ORDER BY count DESC;'):
        #        n_bad_blocks += 1
        #        n_edges_skipped += combs(count, 2)
        #    pprint >>fh, '\t'.join([str(block), str(count), mem(count), ids2value[block]])

        #cu_w.execute('''
        #    CREATE TABLE clean_profile
        #        AS SELECT p.id, p.block FROM profile AS p
        #           WHERE NOT EXISTS(SELECT * FROM badblocks WHERE block=p.block);
        #''')
        #db_w.commit()

        #n_clean_associations = cu_w.execute('SELECT count(*) FROM clean_profile;').fetchone()[0]
        #n_clean_blocks = cu_w.execute('SELECT count(DISTINCT block) FROM clean_profile;').fetchone()[0]
        #n_avg_clean_assoc_per_block = float(n_clean_associations) / n_clean_blocks

        #info('bad blocks removed.', n_bad_blocks=n_bad_blocks, n_edges_skipped=n_edges_skipped, \
        #     n_clean_associations=n_clean_associations, n_clean_blocks=n_clean_blocks, \
        #     n_avg_clean_assoc_per_block=n_avg_clean_assoc_per_block)

    with timer('Graph'):
        info('creating graph...')

        # add all edges of the graph by adding them to a hashtable.
        # use some hacks to keep the memory usage low.
        #
        packer = struct.Struct('>I').pack
        unpacker = struct.Struct('>I').unpack
        def pack(n):
            return packer(n)

        def unpack(s):
            return unpacker(s)[0]

        def add_edges(block, ids):
            if len(ids) < 2:
                return 0L

            #print 'adding:', block, ids

            b_block = pack(block)

            ids = list(set(ids))
            ids.sort()
            b_ids = map(pack, ids)

            n_edges = 0L
            wb = leveldb.WriteBatch()

            for edge in itertools.combinations(b_ids, 2):
                wb.Put(edge[0] + edge[1] + b_block, '')
                n_edges += 1

            db_e.Write(wb)

            return n_edges

        with timer('meta 2-insert'):

            n_edges = 0L
            last_block = None
            block_members = []

            for _id, block in cu_w.execute('SELECT id, block FROM profile ORDER BY block;'):
                if block != last_block:
                    n_edges += add_edges(last_block, block_members)
                    last_block = block
                    block_members = [_id]

                else:
                    block_members.append(_id)

            if block_members:
                n_edges += add_edges(last_block, block_members)

            info('edges inserted.', n_edges=n_edges)

        #cu_w.execute('DROP TABLE clean_profile;')
        #db_w.commit()

        #info('temporary table "clean_profile" dropped.')

        with timer('meta 2-counting'):

            info('calculate edge weights...')

            # scan through all edges and count them to calculate their edge weight.
            # calculate their average.
            #
            cu_w.execute('''
                CREATE TABLE edges (
                    n1 integer not null,
                    n2 integer not null,
                    weight integer
                );
            ''')
            db_w.commit()

            b_pre_edge = '\x00'*12
            b_post_edge = '\xff'*12

            last_edge = b_post_edge
            weight = 0L
            n_distinct_edges = 0L
            total_weight = 0L

            edges = db_e.RangeIter(key_from=b_pre_edge, key_to=b_post_edge, include_value=False)
            for edge in edges:
                if edge.startswith(last_edge):
                    weight += 1
                else:
                    if weight:
                        total_weight += weight
                        n1 = unpack(last_edge[:4])
                        n2 = unpack(last_edge[4:])
                        cu_w.execute('INSERT INTO edges (n1, n2, weight) VALUES (?,?,?);', (n1, n2, weight))
                    weight = 1L
                    n_distinct_edges += 1
                    last_edge = edge[:8]

            if weight:
                n1 = unpack(last_edge[:4])
                n2 = unpack(last_edge[4:])
                total_weight += weight
                cu_w.execute('INSERT INTO edges (n1, n2, weight) VALUES (?,?,?);', (n1, n2, weight))

            db_w.commit()

            avg_weight = float(total_weight) / n_distinct_edges

            info('edges counted up.', n_distinct_edges=n_distinct_edges, total_weight=total_weight, avg_weight=avg_weight)

            stats['Distinct Edges.N'] = n_distinct_edges
            stats['Total Weight.N'] = total_weight
            stats['Average Weight.N'] = avg_weight

    with timer('Pruning'):
        info('pruning graph...')

        cu_w.execute('''
            DELETE FROM edges WHERE weight < ?;
        ''', (avg_weight,))
        db_w.commit()

        #n_edges_remaining = cu_w.execute('SELECT count(*) FROM edges;').fetchone()[0]

        #info('graph pruned.', n_edges_remaining=n_edges_remaining)

        #with codecs.open('edges.txt', 'w', 'utf-8') as fh:
        #    for p1, p2, w in cu_w.execute('SELECT n1, n2, weight FROM edges ORDER BY n1, n2;'):
        #        print >>fh, p1, p2, w

        info('edges saved.')


    with timer('Scoring'):
        info('scoring metablocking run...')

        # calculate the f-measure for the output blocks.
        # calculate the accuracy and efficiency of the current metablocking run.
        # 1. PC "pair completeness": Dout / Din
        #    with D.. = number duplicates that share at least one block.
        #
        # 2. RR "reduction ratio": 1 - (Cout / Cin)
        #    with C.. = number of comparisons
        #
        # 3. PQ "pairs quality": Dout / Cout

        #cluster_pairs = set(cu_w.execute('''
        #    SELECT DISTINCT p1.id, p2.id FROM profile AS p1, profile AS p2
        #      WHERE p1.cluster = p2.cluster AND
        #            p1.id < p2.id;
        #''').fetchall())

        ground_truth = map(lambda entities: set(itertools.combinations(sorted(entities), 2)), clusters.values())
        while len(ground_truth) > 1:
            for _ in xrange(len(ground_truth)/2):
                tmp = ground_truth.pop(0)
                tmp = tmp.union(ground_truth.pop(0))
                ground_truth.append(tmp)
        ground_truth = ground_truth[0]

        print >>sys.stderr, '# ground_truth:', len(ground_truth)
        stats['Ground Truth Entity Pairs.N'] = len(ground_truth)

        meta_pairs = set(cu_w.execute('''
            SELECT n1, n2 FROM edges;
        ''').fetchall())
        stats['Output Entity Pairs.N'] = len(meta_pairs)

        n_cluster_pairs = len(ground_truth)
        n_meta_pairs = len(meta_pairs)

        #with codecs.open('cluster-pairs.txt', 'w', 'utf-8') as fh:
        #    for p1, p2 in sorted(cluster_pairs):
        #        print >>fh, p1, p2

        #with codecs.open('metablocking-pairs.txt', 'w', 'utf-8') as fh:
        #    for p1, p2 in sorted(meta_pairs):
        #        print >>fh, p1, p2

        # true positive: PAIR found in INPUT and OUTPUT blocks.
        n_true_positive = len(ground_truth.intersection(meta_pairs))
        # false positive: PAIR found in OUTPUT but not in INPUT.
        n_false_positive = len(meta_pairs - ground_truth)
        # true negative: PAIR found neither in INPUT nor OUTPUT.
        n_true_negative = '------'
        # false negative: PAIR found in INPUT but not in OUTPUT.
        n_false_negative = len(ground_truth - meta_pairs)

        stats['True Positives.N'] = n_true_positive
        stats['False Positives.N'] = n_false_positive
        stats['False Negatives.N'] = n_false_negative


        # recall and precision:
        recall = float(n_true_positive) / (n_true_positive + n_false_negative)
        precision = float(n_true_positive) / (n_true_positive + n_false_positive)

        # f-measure
        f_measure = 2 * precision * recall / (precision + recall)

        stats['Recall.Recall'] = recall
        stats['Precision.Precision'] = precision
        stats['F-Measure.F-Measure'] = f_measure

    with timer('post 1-paperstats'):
        # PC
        cluster_pairs_sharing_block = set(cu_w.execute('''
            SELECT DISTINCT p1.id, p2.id FROM profile AS p1, profile AS p2
                WHERE p1.cluster = p2.cluster AND
                      p1.block = p2.block AND
                      p1.id < p2.id;
        ''').fetchall())

        Din = len(cluster_pairs_sharing_block)
        Dout = len(meta_pairs.intersection(cluster_pairs_sharing_block))
        PC = float(Dout) / Din

        # RR
        n_edges_remaining = cu_w.execute('SELECT count(*) FROM edges;').fetchone()[0]

        #RR_complete = 1.0 - float(n_edges_remaining) / (n_edges + n_edges_skipped)
        #RR_cheating = 1.0 - float(n_edges_remaining) / n_edges
        RR = 1.0 - float(n_edges_remaining) / n_edges

        # PQ
        PQ = float(n_true_positive) / n_edges

        stats['PC'] = PC
        stats['RR'] = RR
        stats['PQ'] = PQ

        info('metablocking run analysed.', \
             _0={
                 'n_cluster_pairs': n_cluster_pairs,
                 'n_meta_pairs': n_meta_pairs,
                 }, \
             _1={
                 'n_true_positive': n_true_positive,
                 'n_false_positive': n_false_positive,
                 'n_true_negative': n_true_negative,
                 'n_false_negative': n_false_negative,
                 }, \
             _2={
                 'precision': precision,
                 'recall': recall,
                 }, \
             _3={
                 'f_measure': f_measure,
                 }, \
             _4={
                 'PC': PC,
                 #'RR_complete': RR_complete,
                 #'RR_cheating': RR_cheating,
                 'RR': RR,
                 'PQ': PQ,
                 })

        info('work completed.')

        cu_w.close()
        db_w.close()

        info('batch.py ended.')

main()

time_stopped = time.clock()
#stats['time_stopped'] = time_stopped
stats['Overall Runtime.Runtime'] = time_stopped - time_started

print 'BATCH', stats
