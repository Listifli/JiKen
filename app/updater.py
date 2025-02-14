# -*- coding: utf-8 -*-
"""
@author: Ambiwlans
@general: JiKen - Kanji testing site
@description: Updater
"""

from flask import current_app

#Debug
import traceback
import pprint

#Data Handling
import pandas as pd
import numpy as np
import pickle

#Models
from .models import TestMaterial, TempTestMaterial, \
    TestLog, QuestionLog, \
    MetaStatistics

from sqlalchemy.dialects.mysql import SMALLINT

#Tools
from sqlalchemy import func
from sqlalchemy.sql import exists
import datetime
from app.utils import sigmoid#, logit
    
from scipy.integrate import quad
from math import ceil

#DB
from app import db

def update_TestQuestionLogs(app):
    #move stuff from redis to SQL (Ql,Tl)
    with app.app_context():
        print("--------LOG UPDATE------------")
#        x = current_app.config['SESSION_REDIS'].scan()
#        print(x)
        for sess in current_app.config['SESSION_REDIS'].scan_iter("session:*"):
            try:
                rdata = current_app.config['SESSION_REDIS'].get(sess)
                if rdata is None:
                    print("Skipping bugged session")
                    #TODO - This shouldn't occur.
                    continue
                
                data = pickle.loads(rdata)
                
                # Skip sessions without attached tests after adding a timeout to clear out old sessions faster
                if (data.get('last_touched', -1) == -1):
                    data['last_touched'] = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    current_app.config['SESSION_REDIS'].set(sess, pickle.dumps(data))
                    print("Added timestamp to new empty session")
                    continue
                
                
                #Check timestamp to see if we should move it to SQL (>TEST_TIMEOUT mins since last touched)
                if datetime.datetime.utcnow() - \
                        datetime.datetime.strptime(data.get('last_touched', datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')), '%Y-%m-%d %H:%M:%S')\
                        < datetime.timedelta(minutes=current_app.config['TEST_TIMEOUT']):
                    if (len(data.get('QuestionLog', [])) == 0):
                        print("Skipping recent empty session")
                        continue
                    print("Skipping active test #" + str(data['TestLog']['id']) + " from: " + str(data['TestLog']['start_time']) + \
                        "   last_touched: " + str(data.get('last_touched')) + "." + \
                        "   " + str(datetime.datetime.utcnow() - datetime.datetime.strptime(data.get('last_touched', datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')), '%Y-%m-%d %H:%M:%S')) + " ago.")
                    continue
                
                #Don't bother recording incomplete tests
                if len(data.get('QuestionLog', [])) < current_app.config['MIN_TEST_LENGTH']:
                    current_app.config['SESSION_REDIS'].delete(sess)
                    print("Trashing pointless short/non test")
                    continue
                
                #Don't save tests with duplicted ids
                if db.session.query(exists().where(TestLog.id == data['TestLog']['id'])).scalar():
                    current_app.config['SESSION_REDIS'].delete(sess)
                    print("Trashing already saved test #"+ str(data['TestLog']['id']))
                    continue
                
                #Create new test
                addTest = TestLog(
                    id = data['TestLog']['id'],
                    a = int(data['TestLog']['a']),
                    t =  data['TestLog']['t'],
                    ip =  data['TestLog']['ip'],
                    start_time =  data['TestLog']['start_time'],
                    number_of_questions = len(data['QuestionLog']))
                db.session.add(addTest)
                db.session.commit()
                
                #Delete old questions first to avoid orphaned questions (delete 1000 more than the max to avoid frequent clears)
                cutoff = max(db.session.query(QuestionLog.id).count() - current_app.config['MAX_QUESTIONS_LOGGED'], 0)
                if cutoff > 0:
                    cutoff_id = db.session.query(QuestionLog).order_by(QuestionLog.id)[cutoff + 1000].id
                    db.session.query(QuestionLog).filter(QuestionLog.id < cutoff_id).delete()
                    print(str(cutoff) + " old questions deleted")
        
                #Bulk dump the question log (only save MAX_QUESTIONS_LOGGED_EACH per test)
                db.engine.execute(
                        QuestionLog.__table__.insert(),
                        [{"testlogid" : addTest.id,
                          "testmaterialid" : q.testmaterialid,
                          "score" : q.score} for i, q in data['QuestionLog'].iterrows()][-current_app.config['MAX_QUESTIONS_LOGGED_EACH']:])
                db.session.commit()
                
                
                print("Upped Test #: " + str(addTest.id) + "-" + str(data['TestLog']['id']) + " with " + str(len(data.get('QuestionLog', 0))) + " questions.")
                      
                ###        
                ### L2R Adjustments (To the redis)
                ###
                
                # Don't L2R wicked outliers
                if addTest.a < 50 or addTest.a > 7000 or addTest.number_of_questions < 20:
                    continue
                
                temptestmat = pd.read_msgpack(current_app.config['SESSION_REDIS'].get('TempTestMaterial'))
                max_rank = len(temptestmat)
                
                for i, q in data['QuestionLog'].iterrows():
                    #find outliers
                    
                    # Gather variables
                    qrank = int(temptestmat[temptestmat['id'] == int(q.testmaterialid)].iloc[0]['L2R_my_rank'])
                    # 1 = order was totally wrong, 0 = totally right. ie: a=1000, q=人, prediction=1, score=1.... errorlevel = 0
                    errorlevel = abs(sigmoid(qrank, addTest.t, addTest.a, 1) - q.score)                    
                    if (errorlevel < current_app.config['ERRORLEVEL_CUTOFF_PCT']): continue                    
                    incdir = int(((qrank < addTest.a) -.5)* 2)
                    shiftsize = int(round((errorlevel * qrank) / current_app.config['SHIFTSIZE_SLOPE']) + 1)

#                    print("outlier found: kanjiid#" + str(q.testmaterialid) + " rank#" + str(qrank))
#                    print("errorlevel: " + str(errorlevel))
#                    print("bumping to: " + str(incdir))
#                    print("shiftsize: " + str(shiftsize))
                    
                    #correct for edge cases
                    if (qrank + shiftsize > max_rank):
                        shiftsize = max_rank - qrank - 1
                    if (qrank - shiftsize < 1):
                        shiftsize = qrank - 1
                    if shiftsize < 1: continue
                    
                    # Update my_rank vals
#                    print("ranks (before):")
#                    pprint.pprint(temptestmat.loc[temptestmat['L2R_my_rank'].between(qrank + ((incdir * shiftsize) - shiftsize)/2, \
#                        qrank + ((incdir * shiftsize) + shiftsize)/2), 'L2R_my_rank'])
                    
                    # reverse increment each question down the line
                    temptestmat.loc[temptestmat['L2R_my_rank'].between(qrank + ((incdir * shiftsize) - shiftsize)/2, \
                        qrank + ((incdir * shiftsize) + shiftsize)/2), 'L2R_my_rank'] -= incdir
                        
                    # increment the target question
                    temptestmat.loc[temptestmat['id'] == int(q.testmaterialid),'L2R_my_rank'] = int(qrank + (incdir * shiftsize))
                    
#                    print("ranks (after):")
#                    pprint.pprint(temptestmat.loc[temptestmat['L2R_my_rank'].between(qrank + ((incdir * shiftsize) - shiftsize)/2, \
#                        qrank + ((incdir * shiftsize) + shiftsize)/2), 'L2R_my_rank'])
                    
                    #Update the redis
                    app.config['SESSION_REDIS'].set('TempTestMaterial', temptestmat.to_msgpack(compress='zlib'))
                db.session.commit()
                
            except Exception: 
                print("Failed to save test to SQL. Session content:")
                pprint.pprint(data)
                traceback.print_exc()
                pass
                
            #Delete session (fail or succeed to add) .... only keep sessions intentionally skipped (with 'continue')
            current_app.config['SESSION_REDIS'].delete(sess)
            
        print("Updated Logs")
        
    
# Setup cron/scheduler to update kanji ranks and defaults
def update_meta(app):
    # update our meta values
    with app.app_context():
        # get the averages after filtering out outliers, tend towards the middle
        current_app.config['SESSION_REDIS'].set('default_tightness', 
            (db.session.query(func.avg(TestLog.t)) \
            .filter(TestLog.number_of_questions > 25) \
            .filter(TestLog.t > 0.001) \
            .filter(TestLog.t < 0.08)[0][0] + .005)/2
            )
        db.session.query(MetaStatistics).first().default_tightness = float(current_app.config['SESSION_REDIS'].get('default_tightness'))
        
        current_app.config['SESSION_REDIS'].set('default_kanji', 
            int((db.session.query(func.avg(TestLog.a)) \
            .filter(TestLog.number_of_questions > 25) \
            .filter(TestLog.a > 100) \
            .filter(TestLog.a < 5000)[0][0] + 2000)/2)
            )
        db.session.query(MetaStatistics).first().default_kanji = int(current_app.config['SESSION_REDIS'].get('default_kanji'))

        avg_known = int(db.session.query(func.avg(TestLog.a)).filter(TestLog.number_of_questions > 10)[0][0])
        current_app.config['SESSION_REDIS'].set('avg_known', avg_known)
        db.session.query(MetaStatistics).first().avg_known = avg_known

        avg_answered = int(db.session.query(func.avg(TestLog.number_of_questions)).filter(TestLog.number_of_questions > 10)[0][0])
        current_app.config['SESSION_REDIS'].set('avg_answered', avg_answered)
        db.session.query(MetaStatistics).first().avg_answered = avg_answered
        
        db.session.commit()
        
        #Calculate Histogram
        # bin count from config, ensure that the end result rounds to nice graphable 100s
        binsize = (ceil((current_app.config['MAX_X'] / current_app.config['HIST_BINS'])/100)*100)
        bins = list(np.arange(0, current_app.config['MAX_X'] + binsize, binsize))
        testlogs = [[t[0],a[0]] for t,a in zip(db.session.query(TestLog.t), db.session.query(TestLog.a))]
        ests = [quad(sigmoid, 0, current_app.config['MAX_X'], args=(*x,1))[0] for x in testlogs]
        current_app.config['SESSION_REDIS'].set('Hist', pd.cut(ests, bins, include_lowest=True,labels=bins[0:-1]).value_counts().to_msgpack(compress='zlib'))
#        print(pd.read_msgpack(current_app.config['SESSION_REDIS'].get('Hist')))
  
    
        print("Successfully Updated Meta vals")
        print("A = " + str(int(current_app.config['SESSION_REDIS'].get('default_kanji'))))
        print("T = " + str(float(current_app.config['SESSION_REDIS'].get('default_tightness'))))
        print("Known = " + str(avg_known))
        print("Answered = " + str(avg_answered))
        
        #L2R update (to temp)
        if app.config['SESSION_REDIS'].get('TempTestMaterial') is not None:
            print("Updating L2R temptestmats")
            temptestmat = pd.read_msgpack(current_app.config['SESSION_REDIS'].get('TempTestMaterial'))
            temptestmat.to_sql("temptestmaterial", db.engine, index=False, if_exists="replace", \
                               dtype={'id': SMALLINT(unsigned=True), 'L2R_my_rank': SMALLINT(unsigned=True)})
            db.session.commit()
        else:
            print("Can't update L2R temptestmats, missing redis")
        
        
        if app.config['PUSH_L2R_LIVE']:
            #Push the L2R update from temp to full and use that in redis right away
            print("Pushing L2R testmats live")
            num_updates_L2R = db.session.query(TestMaterial).join(TempTestMaterial, TempTestMaterial.id == TestMaterial.id).filter(TempTestMaterial.L2R_my_rank != TestMaterial.my_rank).count()
            print("Found " + str(num_updates_L2R) + " items to update.")
            db.engine.execute("""update temptestmaterial
                              join testmaterial on testmaterial.id = temptestmaterial.id
                              set my_rank = NULL;""")
            db.engine.execute("""update temptestmaterial
                              join testmaterial on testmaterial.id = temptestmaterial.id
                              set my_rank = L2R_my_rank;""")
            db.session.commit()
            
            #refresh redis w/ new data
            current_app.config['SESSION_REDIS'].set('TestMaterial', pd.read_sql(db.session.query(TestMaterial).statement,db.engine).to_msgpack(compress='zlib'))
            print("Refreshed redis TestMaterial with new data")
        
# Clear ancient logs
def clear_old_logs(app):
    with app.app_context():
        print("--------LOG CLEANUP------------")
        #Delete old questions first to avoid orphaned questions (delete 1000 more than the max to avoid frequent clears)
        cutoff = max(db.session.query(QuestionLog.id).count() - current_app.config['MAX_QUESTIONS_LOGGED'], 0)
        if cutoff > 0:
            cutoff_id = db.session.query(QuestionLog).order_by(QuestionLog.id)[cutoff + 1000].id
            db.session.query(QuestionLog).filter(QuestionLog.id < cutoff_id).delete()
            print(str(cutoff) + " old questions deleted")
        
        cutoff = max(db.session.query(TestLog.id).count() - current_app.config['MAX_TESTS_LOGGED'], 0)
        if cutoff > 0:
            cutoff_id = db.session.query(TestLog).order_by(TestLog.id)[cutoff + 1000].id
            db.session.query(TestLog).filter(TestLog.id < cutoff_id).delete()
            print(str(cutoff) + " old tests deleted")
        
        db.session.commit()
        
# Reformat base DB taken from KANJIDIC
def initial_DB_reformat():
    data = db.session.query(TestMaterial).all()    
    ranks = [r for r, in db.session.query(TestMaterial.my_rank)]
    
    for item in data:
        if "Kyōiku-Jōyō (1st" in item.grade:
            item.grade = 1
        elif "Kyōiku-Jōyō (2nd" in item.grade:
            item.grade = 2
        elif "Kyōiku-Jōyō (3rd" in item.grade:
            item.grade = 3
        elif "Kyōiku-Jōyō (4th" in item.grade:
            item.grade = 4
        elif "Kyōiku-Jōyō (5th" in item.grade:
            item.grade = 5
        elif "Kyōiku-Jōyō (6th" in item.grade:
            item.grade = 6
        elif "Jōyō (1st" in item.grade:
            item.grade = 7
        elif "Jōyō (2nd" in item.grade:
            item.grade = 8
        elif "Jōyō (3rd" in item.grade:
            item.grade = 9
        elif "Kyōiku-Jōyō (high" in item.grade:
            item.grade = 10
        elif "Hyōgaiji (former Jinmeiyō candidate)" in item.grade:
            item.grade = 11
        elif "Jinmeiyō (used in names)" in item.grade:
            item.grade = 13
        elif "i" in item.grade:
            item.grade = 14
            
        if "1" in (item.jlpt or ""):
            item.jlpt = 1
        elif "2" in (item.jlpt or ""):
            item.jlpt = 2
        elif "3" in (item.jlpt or ""):
            item.jlpt = 3
        elif "4" in (item.jlpt or ""):
            item.jlpt = 4
        elif "5" in (item.jlpt or ""):
            item.jlpt = 5
        else:
            item.jlpt = 6
            
#        item.meaning = item.meaning.replace(";","; ")
        
    # Find some good starting point for rankings of kanji
    for i in range(len(data)):
        # Use the frequency rates as a base
        ranks[i] = int(data[i].frequency or 0)
        
        if data[i].frequency is None:
            ranks[i] = 4000
        
        # Penalize based on JLPT, kanken, jouyou levels
        ranks[i] += int(data[i].grade) * 50
        ranks[i] -= (int(data[i].jlpt)-6) * 50
        if data[i].kanken:
            ranks[i] -= (int(data[i].kanken)+1) * 50 if data[i].kanken.isdigit() else 0
            if data[i].kanken == "pre-2":
                ranks[i] -= 3 * 50
            elif data[i].kanken == "2":
                ranks[i] += 50
            elif data[i].kanken == "pre-1":
                ranks[i] -= 1 * 50
    
    t = np.array(ranks).argsort()
    ranks = (t.argsort() + 1).tolist()
    
    for i in range(len(data)):
        data[i].my_rank = ranks[i]
    
    print("Initial DB reform complete!")