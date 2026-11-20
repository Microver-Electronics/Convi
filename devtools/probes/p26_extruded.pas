procedure Body;
var Doc, Board, B, C, M: Variant; R: TCoordRect;
begin
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  B := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
  B.Layer := eMechanical13;
  B.BodyProjection := eBoardSide_Top;
  C := PCBServer.PCBContourFactory;
  C.AddPoint(MMsToCoord(50), MMsToCoord(50)); C.AddPoint(MMsToCoord(54), MMsToCoord(50));
  C.AddPoint(MMsToCoord(54), MMsToCoord(52)); C.AddPoint(MMsToCoord(50), MMsToCoord(52));
  try
    B.SetOutlineContour(C);
    L('outline set');
  except
    L('SetOutlineContour failed: ' + ExceptionMessage);
  end;
  try
    M := B.ModelFactory_CreateExtruded(0, MMsToCoord(1.5), $808080);
    B.Model := M;
    L('extruded model ok');
  except
    L('extruded failed: ' + ExceptionMessage);
  end;
  try
    B.StandoffHeight := 0;
    B.OverallHeight := MMsToCoord(1.5);
  except
    L('heights: ' + ExceptionMessage);
  end;
  Board.AddPCBObject(B);
  R := B.BoundingRectangle;
  L('bbox ' + FloatToStr(CoordToMMs(R.Left)) + ',' + FloatToStr(CoordToMMs(R.Bottom)) + ' - ' + FloatToStr(CoordToMMs(R.Right)) + ',' + FloatToStr(CoordToMMs(R.Top)) + ' oh=' + FloatToStr(CoordToMMs(B.OverallHeight)));
  Client.CloseDocument(Doc);
end;
