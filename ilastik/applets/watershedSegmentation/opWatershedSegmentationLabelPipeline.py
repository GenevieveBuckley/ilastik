from lazyflow.graph import Operator, InputSlot, OutputSlot
from ilastik.applets.pixelClassification.opPixelClassification import OpLabelPipeline

import logging
logger = logging.getLogger(__name__)

class OpWatershedSegmentationLabelPipeline( Operator ):
    """
    operator class, that handles the Label Pipeline and the connections to it.
    the opLabelPipeline handles the connections to the opCompressedUserLabelArray, 
    which is responsable for everything of the caching and so on
    """
    RawData     = InputSlot()
    SeedInput   = InputSlot()
    SeedOutput  = OutputSlot()
    NonZeroBlocks = OutputSlot()
    
    
    def __init__(self, *args, **kwargs):
        super( OpWatershedSegmentationLabelPipeline, self ).__init__( *args, **kwargs )
        
        self.opLabelPipeline = OpLabelPipeline(parent=self)
        self.opLabelPipeline.RawImage.connect( self.RawData )
        self.opLabelPipeline.LabelInput.connect( self.SeedInput )
        self.opLabelPipeline.DeleteLabel.setValue( -1 )

        #Output
        self.SeedOutput.connect( self.opLabelPipeline.Output )
        self.NonZeroBlocks.connect( self.opLabelPipeline.nonzeroBlocks )

    def setupOutputs(self):
        '''
        self.SeedOutput.meta.assignFrom(self.SeedInput.meta)
        # output of the vigra.analysis.watershedNew is uint32, therefore it should be uint 32 as
        # well, otherwise it will break with the cached image 
        self.SeedOutput.meta.dtype = np.uint8
        #only one channel as output
        #self.SeedOutput.meta.shape = self.Boundaries.meta.shape[:-1] + (1,)
        #TODO maybe bad with more than 255 labels
        #self.SeedOutput.meta.drange = (0,255)
        '''
        pass

    def setInSlot(self, slot, subindex, roi, value):
        pass

    def execute(self, slot, subindex, roi, result):
        assert False, "Shouldn't get here.  Output is assigned a value in setupOutputs()"


    def propagateDirty(self, slot, subindex, roi):
        print "LabelPipeline dirty: " + slot.name
        self.SeedOutput.setDirty()
        pass    


