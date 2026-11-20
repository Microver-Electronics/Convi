procedure LogBody(Body: Variant; Tag: String);
var R: TCoordRect;
begin
  R := Body.BoundingRectangle;
  L(Tag + ': L=' + FloatToStr(CoordToMMs(R.Left)) + ' R=' + FloatToStr(CoordToMMs(R.Right)) + ' B=' + FloatToStr(CoordToMMs(R.Bottom)) + ' T=' + FloatToStr(CoordToMMs(R.Top)) + ' sh=' + FloatToStr(CoordToMMs(Body.StandoffHeight)) + ' oh=' + FloatToStr(CoordToMMs(Body.OverallHeight)) + ' layer=' + IntToStr(Body.Layer));
end;

procedure Try1(Board: Variant; RX, RY, RZ: Double; Tag: String);
var Body, Model: Variant;
begin
  Body := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
  Model := Body.ModelFactory_FromFilename('%ROOT%\testdata\models\model.step', False);
  Model.SetState(RX, RY, RZ, 0);
  Body.Model := Model;
  Body.SetState_FromModel;
  Body.Layer := eBottomLayer;
  Board.AddPCBObject(Body);
  LogBody(Body, Tag);
end;

procedure Body;
var
  Doc, Board, Comp, Pad, Body, Model: Variant;
begin
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  Try1(Board, 0, 0, 0, 'bot r000');
  Try1(Board, 0, 0, 90, 'bot rz90');
  Try1(Board, 90, 0, 0, 'bot rx90');

  // component flip test
  Comp := PCBServer.PCBObjectFactory(eComponentObject, eNoDimension, eCreate_Default);
  Comp.X := MMsToCoord(50); Comp.Y := MMsToCoord(50);
  Comp.Layer := eTopLayer;
  Comp.Pattern := 'FLIPTEST';
  Board.AddPCBObject(Comp);
  Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
  Pad.X := MMsToCoord(52); Pad.Y := MMsToCoord(51);
  Pad.TopXSize := MMsToCoord(1); Pad.TopYSize := MMsToCoord(0.5); Pad.TopShape := eRectangular;
  Pad.Layer := eTopLayer; Pad.Name := '1';
  Board.AddPCBObject(Pad);
  Comp.AddPCBObject(Pad);
  Body := PCBServer.PCBObjectFactory(eComponentBodyObject, eNoDimension, eCreate_Default);
  Model := Body.ModelFactory_FromFilename('%ROOT%\testdata\models\model.step', False);
  Model.SetState(0, 0, 0, 0);
  Body.Model := Model;
  Body.SetState_FromModel;
  Body.Layer := eTopLayer;
  Body.MoveByXY(MMsToCoord(50), MMsToCoord(50));
  Board.AddPCBObject(Body);
  Comp.AddPCBObject(Body);
  LogBody(Body, 'comp top body');
  Comp.Rotation := 30;
  L('rot30 pad X=' + FloatToStr(CoordToMMs(Pad.X)) + ' Y=' + FloatToStr(CoordToMMs(Pad.Y)) + ' padrot=' + FloatToStr(Pad.Rotation));
  Comp.Rotation := 0;
  Comp.Layer := eBottomLayer;
  L('after flip: comp rot=' + FloatToStr(Comp.Rotation) + ' pad X=' + FloatToStr(CoordToMMs(Pad.X)) + ' Y=' + FloatToStr(CoordToMMs(Pad.Y)) + ' padlayer=' + IntToStr(Pad.Layer) + ' padrot=' + FloatToStr(Pad.Rotation));
  LogBody(Body, 'comp flipped body');
  Model := Body.Model;
  L('model state after flip: ' );
  Comp.Rotation := 90;
  L('bottom rot90: pad X=' + FloatToStr(CoordToMMs(Pad.X)) + ' Y=' + FloatToStr(CoordToMMs(Pad.Y)) + ' padrot=' + FloatToStr(Pad.Rotation));
  LogBody(Body, 'comp flipped rot90 body');
  Doc.SetFileName('%ROOT%\devtools\_probe_run\p07.PcbDoc');
  Doc.DoFileSave('');
  Client.CloseDocument(Doc);
end;
